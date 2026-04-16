"""Operator control surface for direct and mailbox-safe runtime mutations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping
from uuid import uuid4

from pydantic import JsonValue

from millrace_ai.contracts import (
    MailboxAddIdeaPayload,
    MailboxAddSpecPayload,
    MailboxAddTaskPayload,
    MailboxCommand,
    MailboxCommandEnvelope,
    Plane,
    RecoveryCounters,
    RuntimeSnapshot,
    SpecDocument,
    TaskDocument,
    WorkItemKind,
)
from millrace_ai.errors import QueueStateError, WorkspaceStateError
from millrace_ai.mailbox import write_mailbox_command
from millrace_ai.paths import WorkspacePaths, bootstrap_workspace, workspace_paths
from millrace_ai.queue_store import QueueStore
from millrace_ai.runtime_lock import (
    clear_stale_runtime_ownership_lock,
    inspect_runtime_ownership_lock,
)
from millrace_ai.state_store import (
    load_recovery_counters,
    load_snapshot,
    reset_forward_progress_counters,
    save_recovery_counters,
    save_snapshot,
    set_execution_status,
    set_planning_status,
)

_STATUS_IDLE = "### IDLE"


@dataclass(frozen=True, slots=True)
class ControlActionResult:
    """Outcome for one control action request."""

    action: MailboxCommand
    mode: str
    applied: bool
    detail: str
    command_id: str | None = None
    mailbox_path: Path | None = None
    artifact_path: Path | None = None


class RuntimeControl:
    """Control API that switches between direct and mailbox-safe mutation paths."""

    def __init__(self, target: WorkspacePaths | Path | str) -> None:
        paths = target if isinstance(target, WorkspacePaths) else workspace_paths(target)
        self.paths = bootstrap_workspace(paths)

    def pause_runtime(self, *, issuer: str = "operator") -> ControlActionResult:
        return self._dispatch(
            command=MailboxCommand.PAUSE,
            issuer=issuer,
            direct_handler=self._pause_direct,
        )

    def resume_runtime(self, *, issuer: str = "operator") -> ControlActionResult:
        return self._dispatch(
            command=MailboxCommand.RESUME,
            issuer=issuer,
            direct_handler=self._resume_direct,
        )

    def stop_runtime(self, *, issuer: str = "operator") -> ControlActionResult:
        return self._dispatch(
            command=MailboxCommand.STOP,
            issuer=issuer,
            direct_handler=self._stop_direct,
        )

    def retry_active(
        self,
        *,
        reason: str = "operator requested retry",
        issuer: str = "operator",
    ) -> ControlActionResult:
        payload = {"reason": reason.strip() or "operator requested retry"}
        return self._dispatch(
            command=MailboxCommand.RETRY_ACTIVE,
            issuer=issuer,
            payload=payload,
            direct_handler=lambda snapshot: self._retry_active_direct(
                snapshot,
                reason=payload["reason"],
                scope=None,
            ),
        )

    def retry_active_planning(
        self,
        *,
        reason: str = "operator requested planning retry",
        issuer: str = "operator",
    ) -> ControlActionResult:
        snapshot = load_snapshot(self.paths)
        if snapshot.active_plane is not Plane.PLANNING:
            active_plane = snapshot.active_plane.value if snapshot.active_plane is not None else "none"
            return ControlActionResult(
                action=MailboxCommand.RETRY_ACTIVE,
                mode="direct",
                applied=False,
                detail=f"planning retry requires active planning work; current active plane is {active_plane}",
            )

        payload = {
            "reason": reason.strip() or "operator requested planning retry",
            "scope": Plane.PLANNING.value,
        }
        return self._dispatch(
            command=MailboxCommand.RETRY_ACTIVE,
            issuer=issuer,
            payload=payload,
            direct_handler=lambda current_snapshot: self._retry_active_direct(
                current_snapshot,
                reason=payload["reason"],
                scope=Plane.PLANNING,
            ),
        )

    def clear_stale_state(
        self,
        *,
        reason: str = "operator requested stale-state clear",
        issuer: str = "operator",
    ) -> ControlActionResult:
        payload = {"reason": reason.strip() or "operator requested stale-state clear"}
        return self._dispatch(
            command=MailboxCommand.CLEAR_STALE_STATE,
            issuer=issuer,
            payload=payload,
            direct_handler=lambda snapshot: self._clear_stale_direct(snapshot, reason=payload["reason"]),
        )

    def reload_config(self, *, issuer: str = "operator") -> ControlActionResult:
        return self._dispatch(
            command=MailboxCommand.RELOAD_CONFIG,
            issuer=issuer,
            direct_handler=self._reload_config_direct,
        )

    def add_task(self, document: TaskDocument, *, issuer: str = "operator") -> ControlActionResult:
        payload = MailboxAddTaskPayload(document=document).model_dump(mode="json")
        return self._dispatch(
            command=MailboxCommand.ADD_TASK,
            issuer=issuer,
            payload=payload,
            direct_handler=lambda snapshot: self._add_task_direct(snapshot, document=document),
        )

    def add_spec(self, document: SpecDocument, *, issuer: str = "operator") -> ControlActionResult:
        payload = MailboxAddSpecPayload(document=document).model_dump(mode="json")
        return self._dispatch(
            command=MailboxCommand.ADD_SPEC,
            issuer=issuer,
            payload=payload,
            direct_handler=lambda snapshot: self._add_spec_direct(snapshot, document=document),
        )

    def add_idea_markdown(
        self,
        *,
        source_name: str,
        markdown: str,
        issuer: str = "operator",
    ) -> ControlActionResult:
        payload_model = MailboxAddIdeaPayload(source_name=source_name, markdown=markdown)
        payload = payload_model.model_dump(mode="json")
        return self._dispatch(
            command=MailboxCommand.ADD_IDEA,
            issuer=issuer,
            payload=payload,
            direct_handler=lambda snapshot: self._add_idea_direct(snapshot, payload=payload_model),
        )

    def _dispatch(
        self,
        *,
        command: MailboxCommand,
        issuer: str,
        direct_handler: Callable[[RuntimeSnapshot], ControlActionResult],
        payload: Mapping[str, JsonValue] | None = None,
    ) -> ControlActionResult:
        snapshot = load_snapshot(self.paths)
        if command is MailboxCommand.CLEAR_STALE_STATE:
            lock_status = inspect_runtime_ownership_lock(self.paths)
            if lock_status.state in {"absent", "stale", "invalid"}:
                return direct_handler(snapshot)
        if self._daemon_owns_workspace():
            return self._enqueue_mailbox_command(command=command, issuer=issuer, payload=payload)
        return direct_handler(snapshot)

    def _add_task_direct(self, snapshot: RuntimeSnapshot, *, document: TaskDocument) -> ControlActionResult:
        destination = QueueStore(self.paths).enqueue_task(document)
        save_snapshot(
            self.paths,
            snapshot.model_copy(
                update={
                    "queue_depth_execution": self._execution_queue_depth(),
                    "updated_at": self._now(),
                }
            ),
        )
        return ControlActionResult(
            action=MailboxCommand.ADD_TASK,
            mode="direct",
            applied=True,
            detail="task queued directly",
            artifact_path=destination,
        )

    def _add_spec_direct(self, snapshot: RuntimeSnapshot, *, document: SpecDocument) -> ControlActionResult:
        destination = QueueStore(self.paths).enqueue_spec(document)
        save_snapshot(
            self.paths,
            snapshot.model_copy(
                update={
                    "queue_depth_planning": self._planning_queue_depth(),
                    "updated_at": self._now(),
                }
            ),
        )
        return ControlActionResult(
            action=MailboxCommand.ADD_SPEC,
            mode="direct",
            applied=True,
            detail="spec queued directly",
            artifact_path=destination,
        )

    def _add_idea_direct(
        self,
        snapshot: RuntimeSnapshot,
        *,
        payload: MailboxAddIdeaPayload,
    ) -> ControlActionResult:
        destination_dir = self.paths.root / "ideas" / "inbox"
        destination_dir.mkdir(parents=True, exist_ok=True)
        destination = destination_dir / payload.source_name
        if destination.exists():
            raise WorkspaceStateError(f"idea document already exists: {destination}")
        destination.write_text(payload.markdown, encoding="utf-8")
        save_snapshot(
            self.paths,
            snapshot.model_copy(
                update={
                    "queue_depth_planning": self._planning_queue_depth(),
                    "updated_at": self._now(),
                }
            ),
        )
        return ControlActionResult(
            action=MailboxCommand.ADD_IDEA,
            mode="direct",
            applied=True,
            detail="idea staged directly",
            artifact_path=destination,
        )

    def _pause_direct(self, snapshot: RuntimeSnapshot) -> ControlActionResult:
        changed = not snapshot.paused
        updated = snapshot.model_copy(update={"paused": True, "updated_at": self._now()})
        save_snapshot(self.paths, updated)
        return ControlActionResult(
            action=MailboxCommand.PAUSE,
            mode="direct",
            applied=changed,
            detail="runtime paused directly",
        )

    def _resume_direct(self, snapshot: RuntimeSnapshot) -> ControlActionResult:
        changed = snapshot.paused
        updated = snapshot.model_copy(update={"paused": False, "updated_at": self._now()})
        save_snapshot(self.paths, updated)
        return ControlActionResult(
            action=MailboxCommand.RESUME,
            mode="direct",
            applied=changed,
            detail="runtime resumed directly",
        )

    def _stop_direct(self, snapshot: RuntimeSnapshot) -> ControlActionResult:
        changed = snapshot.process_running or not snapshot.stop_requested
        updated = snapshot.model_copy(
            update={
                "process_running": False,
                "stop_requested": True,
                "updated_at": self._now(),
            }
        )
        save_snapshot(self.paths, updated)
        return ControlActionResult(
            action=MailboxCommand.STOP,
            mode="direct",
            applied=changed,
            detail="runtime stop requested directly",
        )

    def _retry_active_direct(
        self,
        snapshot: RuntimeSnapshot,
        *,
        reason: str,
        scope: Plane | None,
    ) -> ControlActionResult:
        if snapshot.active_work_item_kind is None or snapshot.active_work_item_id is None:
            return ControlActionResult(
                action=MailboxCommand.RETRY_ACTIVE,
                mode="direct",
                applied=False,
                detail="no active work item to retry",
            )
        if scope is not None and snapshot.active_plane is not scope:
            active_plane = snapshot.active_plane.value if snapshot.active_plane is not None else "none"
            return ControlActionResult(
                action=MailboxCommand.RETRY_ACTIVE,
                mode="direct",
                applied=False,
                detail=f"{scope.value} retry requires matching active plane; current active plane is {active_plane}",
            )

        queue = QueueStore(self.paths)
        work_item_kind = snapshot.active_work_item_kind
        work_item_id = snapshot.active_work_item_id

        try:
            self._requeue_active_item(queue, work_item_kind=work_item_kind, work_item_id=work_item_id, reason=reason)
        except QueueStateError as exc:
            return ControlActionResult(
                action=MailboxCommand.RETRY_ACTIVE,
                mode="direct",
                applied=False,
                detail=str(exc),
            )

        self._reset_runtime_to_idle(clear_stop_requested=False, clear_paused=False)
        reset_forward_progress_counters(
            self.paths,
            work_item_kind=work_item_kind,
            work_item_id=work_item_id,
        )

        return ControlActionResult(
            action=MailboxCommand.RETRY_ACTIVE,
            mode="direct",
            applied=True,
            detail=f"active {work_item_kind.value} {work_item_id} requeued",
        )

    def _clear_stale_direct(
        self,
        snapshot: RuntimeSnapshot,
        *,
        reason: str,
    ) -> ControlActionResult:
        queue = QueueStore(self.paths)
        requeued_count = self._requeue_all_active_items(queue, reason=reason)
        had_counters = bool(load_recovery_counters(self.paths).entries)
        lock_clear_result = clear_stale_runtime_ownership_lock(self.paths)

        self._reset_runtime_to_idle(clear_stop_requested=True, clear_paused=True)
        save_recovery_counters(self.paths, RecoveryCounters())

        applied = (
            requeued_count > 0
            or had_counters
            or snapshot.active_stage is not None
            or snapshot.paused
            or snapshot.stop_requested
            or lock_clear_result.cleared
        )
        return ControlActionResult(
            action=MailboxCommand.CLEAR_STALE_STATE,
            mode="direct",
            applied=applied,
            detail=(
                f"cleared stale runtime state; requeued={requeued_count}; "
                f"runtime_ownership_lock={lock_clear_result.reason}"
            ),
        )

    def _reload_config_direct(self, snapshot: RuntimeSnapshot) -> ControlActionResult:
        del snapshot
        return ControlActionResult(
            action=MailboxCommand.RELOAD_CONFIG,
            mode="direct",
            applied=False,
            detail="no daemon running; reload request not enqueued",
        )

    def _requeue_all_active_items(self, queue: QueueStore, *, reason: str) -> int:
        requeued_count = 0
        for path in sorted(self.paths.tasks_active_dir.glob("*.md")):
            try:
                queue.requeue_task(path.stem, reason=reason)
            except QueueStateError:
                continue
            requeued_count += 1
        for path in sorted(self.paths.specs_active_dir.glob("*.md")):
            try:
                queue.requeue_spec(path.stem, reason=reason)
            except QueueStateError:
                continue
            requeued_count += 1
        for path in sorted(self.paths.incidents_active_dir.glob("*.md")):
            try:
                queue.requeue_incident(path.stem, reason=reason)
            except QueueStateError:
                continue
            requeued_count += 1
        return requeued_count

    def _requeue_active_item(
        self,
        queue: QueueStore,
        *,
        work_item_kind: WorkItemKind,
        work_item_id: str,
        reason: str,
    ) -> None:
        if work_item_kind is WorkItemKind.TASK:
            queue.requeue_task(work_item_id, reason=reason)
            return
        if work_item_kind is WorkItemKind.SPEC:
            queue.requeue_spec(work_item_id, reason=reason)
            return
        queue.requeue_incident(work_item_id, reason=reason)

    def _reset_runtime_to_idle(self, *, clear_stop_requested: bool, clear_paused: bool) -> None:
        snapshot = load_snapshot(self.paths)
        update: dict[str, object] = {
            "process_running": False,
            "active_plane": None,
            "active_stage": None,
            "active_run_id": None,
            "active_work_item_kind": None,
            "active_work_item_id": None,
            "active_since": None,
            "current_failure_class": None,
            "troubleshoot_attempt_count": 0,
            "mechanic_attempt_count": 0,
            "fix_cycle_count": 0,
            "consultant_invocations": 0,
            "execution_status_marker": _STATUS_IDLE,
            "planning_status_marker": _STATUS_IDLE,
            "queue_depth_execution": self._execution_queue_depth(),
            "queue_depth_planning": self._planning_queue_depth(),
            "updated_at": self._now(),
        }
        if clear_paused:
            update["paused"] = False
        if clear_stop_requested:
            update["stop_requested"] = False

        save_snapshot(self.paths, snapshot.model_copy(update=update))
        set_execution_status(self.paths, _STATUS_IDLE)
        set_planning_status(self.paths, _STATUS_IDLE)

    def _enqueue_mailbox_command(
        self,
        *,
        command: MailboxCommand,
        issuer: str,
        payload: Mapping[str, JsonValue] | None = None,
    ) -> ControlActionResult:
        command_id = self._command_id(command)
        envelope = MailboxCommandEnvelope(
            command_id=command_id,
            command=command,
            issued_at=self._now(),
            issuer=issuer,
            payload=dict(payload or {}),
        )
        mailbox_path = write_mailbox_command(self.paths, envelope)
        return ControlActionResult(
            action=command,
            mode="mailbox",
            applied=False,
            detail="queued for daemon processing",
            command_id=command_id,
            mailbox_path=mailbox_path,
        )

    def _command_id(self, command: MailboxCommand) -> str:
        timestamp_ms = int(self._now().timestamp() * 1000)
        return f"{command.value}-{timestamp_ms}-{uuid4().hex[:8]}"

    def _daemon_owns_workspace(self) -> bool:
        status = inspect_runtime_ownership_lock(self.paths)
        return status.state == "active"

    def _execution_queue_depth(self) -> int:
        return len(tuple(self.paths.tasks_queue_dir.glob("*.md")))

    def _planning_queue_depth(self) -> int:
        specs = len(tuple(self.paths.specs_queue_dir.glob("*.md")))
        incidents = len(tuple(self.paths.incidents_incoming_dir.glob("*.md")))
        return specs + incidents

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)


__all__ = ["ControlActionResult", "RuntimeControl"]
