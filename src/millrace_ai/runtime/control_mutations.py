"""Direct control mutations that operate on offline workspace state."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Generic, TypeVar

from millrace_ai.config import load_runtime_config
from millrace_ai.contracts import (
    ActiveRunState,
    MailboxAddIdeaPayload,
    MailboxCommand,
    Plane,
    RecoveryCounters,
    RuntimeSnapshot,
    SpecDocument,
    TaskDocument,
    WorkItemKind,
)
from millrace_ai.errors import QueueStateError, WorkspaceStateError
from millrace_ai.paths import WorkspacePaths
from millrace_ai.queue_store import QueueStore
from millrace_ai.runtime.active_runs import active_run_for_plane, snapshot_without_active_plane
from millrace_ai.runtime.control_mailbox import ControlActionResultFactory
from millrace_ai.runtime.pause_state import (
    OPERATOR_PAUSE_SOURCE,
    USAGE_GOVERNANCE_PAUSE_SOURCE,
    add_pause_source,
    has_pause_source,
    remove_pause_source,
)
from millrace_ai.runtime.snapshot_state import IDLE_STATUS_MARKER, idle_snapshot_update
from millrace_ai.runtime.usage_governance import (
    UsageGovernanceState,
    evaluate_usage_governance,
    load_usage_governance_state,
)
from millrace_ai.runtime_lock import clear_stale_runtime_ownership_lock
from millrace_ai.state_store import (
    load_recovery_counters,
    reset_forward_progress_counters,
    save_recovery_counters,
    save_snapshot,
    set_execution_status,
    set_learning_status,
    set_planning_status,
)

ResultT = TypeVar("ResultT")


class DirectControlMutations(Generic[ResultT]):
    """Apply direct control mutations when no active daemon owns the workspace."""

    def __init__(
        self,
        paths: WorkspacePaths,
        *,
        result_factory: ControlActionResultFactory[ResultT],
        now: Callable[[], datetime],
    ) -> None:
        self.paths = paths
        self._result_factory = result_factory
        self._now = now

    def add_task(self, snapshot: RuntimeSnapshot, *, document: TaskDocument) -> ResultT:
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
        return self._result_factory(
            action=MailboxCommand.ADD_TASK,
            mode="direct",
            applied=True,
            detail="task queued directly",
            artifact_path=destination,
        )

    def add_spec(self, snapshot: RuntimeSnapshot, *, document: SpecDocument) -> ResultT:
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
        return self._result_factory(
            action=MailboxCommand.ADD_SPEC,
            mode="direct",
            applied=True,
            detail="spec queued directly",
            artifact_path=destination,
        )

    def add_idea(self, snapshot: RuntimeSnapshot, *, payload: MailboxAddIdeaPayload) -> ResultT:
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
        return self._result_factory(
            action=MailboxCommand.ADD_IDEA,
            mode="direct",
            applied=True,
            detail="idea staged directly",
            artifact_path=destination,
        )

    def pause(self, snapshot: RuntimeSnapshot) -> ResultT:
        changed = not has_pause_source(snapshot, OPERATOR_PAUSE_SOURCE)
        updated = add_pause_source(snapshot, source=OPERATOR_PAUSE_SOURCE, now=self._now())
        save_snapshot(self.paths, updated)
        return self._result_factory(
            action=MailboxCommand.PAUSE,
            mode="direct",
            applied=changed,
            detail="runtime paused directly",
        )

    def resume(self, snapshot: RuntimeSnapshot) -> ResultT:
        state = self._current_usage_governance_state(snapshot)
        governance_blocked = bool(state.active_blockers)
        if governance_blocked and not has_pause_source(snapshot, USAGE_GOVERNANCE_PAUSE_SOURCE):
            snapshot = add_pause_source(
                snapshot,
                source=USAGE_GOVERNANCE_PAUSE_SOURCE,
                now=self._now(),
            )
        changed = has_pause_source(snapshot, OPERATOR_PAUSE_SOURCE)
        updated = remove_pause_source(snapshot, source=OPERATOR_PAUSE_SOURCE, now=self._now())
        save_snapshot(self.paths, updated)
        if governance_blocked:
            return self._result_factory(
                action=MailboxCommand.RESUME,
                mode="direct",
                applied=False,
                detail="runtime resume blocked by usage governance",
            )
        return self._result_factory(
            action=MailboxCommand.RESUME,
            mode="direct",
            applied=changed,
            detail="runtime resumed directly",
        )

    def _current_usage_governance_state(self, snapshot: RuntimeSnapshot) -> UsageGovernanceState:
        try:
            config = load_runtime_config(self.paths.runtime_root / "millrace.toml")
            return evaluate_usage_governance(
                self.paths,
                config=config,
                now=self._now(),
                daemon_session_id=None,
                paused_by_governance=has_pause_source(
                    snapshot,
                    USAGE_GOVERNANCE_PAUSE_SOURCE,
                ),
            )
        except Exception:
            return load_usage_governance_state(self.paths)

    def stop(self, snapshot: RuntimeSnapshot) -> ResultT:
        changed = snapshot.process_running or not snapshot.stop_requested
        self._reset_runtime_to_idle(
            snapshot,
            process_running=False,
            clear_stop_requested=True,
            clear_paused=True,
        )
        return self._result_factory(
            action=MailboxCommand.STOP,
            mode="direct",
            applied=changed,
            detail="runtime stopped directly",
        )

    def retry_active(
        self,
        snapshot: RuntimeSnapshot,
        *,
        reason: str,
        scope: Plane | None,
    ) -> ResultT:
        active_run = self._retry_active_run(snapshot, scope=scope)
        if active_run is None:
            return self._result_factory(
                action=MailboxCommand.RETRY_ACTIVE,
                mode="direct",
                applied=False,
                detail=self._retry_active_missing_detail(snapshot, scope=scope),
            )
        if scope is None and len(snapshot.active_runs_by_plane) > 1:
            return self._result_factory(
                action=MailboxCommand.RETRY_ACTIVE,
                mode="direct",
                applied=False,
                detail="multiple active planes; retry-active requires a plane scope",
            )
        if active_run.work_item_kind is None or active_run.work_item_id is None:
            return self._result_factory(
                action=MailboxCommand.RETRY_ACTIVE,
                mode="direct",
                applied=False,
                detail=f"active {active_run.plane.value} run is not a retryable work item",
            )

        queue = QueueStore(self.paths)
        work_item_kind = active_run.work_item_kind
        work_item_id = active_run.work_item_id

        try:
            self._requeue_active_item(
                queue,
                work_item_kind=work_item_kind,
                work_item_id=work_item_id,
                reason=reason,
            )
        except QueueStateError as exc:
            return self._result_factory(
                action=MailboxCommand.RETRY_ACTIVE,
                mode="direct",
                applied=False,
                detail=str(exc),
            )

        self._clear_retry_active_run(snapshot, active_run.plane)
        reset_forward_progress_counters(
            self.paths,
            work_item_kind=work_item_kind,
            work_item_id=work_item_id,
        )
        return self._result_factory(
            action=MailboxCommand.RETRY_ACTIVE,
            mode="direct",
            applied=True,
            detail=f"active {work_item_kind.value} {work_item_id} requeued",
        )

    def _retry_active_run(
        self,
        snapshot: RuntimeSnapshot,
        *,
        scope: Plane | None,
    ) -> ActiveRunState | None:
        if scope is not None:
            return active_run_for_plane(snapshot, scope)
        if len(snapshot.active_runs_by_plane) == 1:
            return next(iter(snapshot.active_runs_by_plane.values()))
        if len(snapshot.active_runs_by_plane) > 1:
            return next(iter(snapshot.active_runs_by_plane.values()))
        if snapshot.active_work_item_kind is None or snapshot.active_work_item_id is None:
            return None
        if snapshot.active_plane is None:
            return None
        return active_run_for_plane(snapshot, snapshot.active_plane)

    def _retry_active_missing_detail(
        self,
        snapshot: RuntimeSnapshot,
        *,
        scope: Plane | None,
    ) -> str:
        if scope is None:
            return "no active work item to retry"
        active_planes = ", ".join(plane.value for plane in snapshot.active_runs_by_plane) or "none"
        return (
            f"{scope.value} retry requires matching active plane; "
            f"current active planes are {active_planes}"
        )

    def _clear_retry_active_run(self, snapshot: RuntimeSnapshot, plane: Plane) -> None:
        remaining = dict(snapshot.active_runs_by_plane)
        remaining.pop(plane, None)
        if not remaining:
            self._reset_runtime_to_idle(
                snapshot,
                process_running=False,
                clear_stop_requested=False,
                clear_paused=False,
            )
            return

        updated = snapshot_without_active_plane(
            snapshot,
            plane=plane,
            now=self._now(),
            current_failure_class=None,
        )
        save_snapshot(self.paths, updated)
        if plane is Plane.EXECUTION:
            set_execution_status(self.paths, IDLE_STATUS_MARKER)
        elif plane is Plane.PLANNING:
            set_planning_status(self.paths, IDLE_STATUS_MARKER)
        else:
            set_learning_status(self.paths, IDLE_STATUS_MARKER)

    def clear_stale(self, snapshot: RuntimeSnapshot, *, reason: str) -> ResultT:
        queue = QueueStore(self.paths)
        requeued_count = self._requeue_all_active_items(queue, reason=reason)
        had_counters = bool(load_recovery_counters(self.paths).entries)
        lock_clear_result = clear_stale_runtime_ownership_lock(self.paths)

        self._reset_runtime_to_idle(
            snapshot,
            process_running=False,
            clear_stop_requested=True,
            clear_paused=True,
        )
        save_recovery_counters(self.paths, RecoveryCounters())

        applied = (
            requeued_count > 0
            or had_counters
            or snapshot.active_stage is not None
            or snapshot.process_running
            or snapshot.paused
            or snapshot.stop_requested
            or lock_clear_result.cleared
        )
        return self._result_factory(
            action=MailboxCommand.CLEAR_STALE_STATE,
            mode="direct",
            applied=applied,
            detail=(
                f"cleared stale runtime state; requeued={requeued_count}; "
                f"runtime_ownership_lock={lock_clear_result.reason}"
            ),
        )

    def reload_config(self, snapshot: RuntimeSnapshot) -> ResultT:
        del snapshot
        return self._result_factory(
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
        for path in sorted(self.paths.learning_requests_active_dir.glob("*.md")):
            try:
                queue.requeue_learning_request(path.stem, reason=reason)
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
        if work_item_kind is WorkItemKind.LEARNING_REQUEST:
            queue.requeue_learning_request(work_item_id, reason=reason)
            return
        queue.requeue_incident(work_item_id, reason=reason)

    def _reset_runtime_to_idle(
        self,
        snapshot: RuntimeSnapshot,
        *,
        process_running: bool,
        clear_stop_requested: bool,
        clear_paused: bool,
    ) -> None:
        updated = snapshot.model_copy(
            update=idle_snapshot_update(
                now=self._now(),
                process_running=process_running,
                queue_depth_execution=self._execution_queue_depth(),
                queue_depth_planning=self._planning_queue_depth(),
                queue_depth_learning=self._learning_queue_depth(),
                clear_stop_requested=clear_stop_requested,
                clear_paused=clear_paused,
            )
        )

        save_snapshot(self.paths, updated)
        set_execution_status(self.paths, IDLE_STATUS_MARKER)
        set_planning_status(self.paths, IDLE_STATUS_MARKER)
        set_learning_status(self.paths, IDLE_STATUS_MARKER)

    def _execution_queue_depth(self) -> int:
        return len(tuple(self.paths.tasks_queue_dir.glob("*.md")))

    def _planning_queue_depth(self) -> int:
        specs = len(tuple(self.paths.specs_queue_dir.glob("*.md")))
        incidents = len(tuple(self.paths.incidents_incoming_dir.glob("*.md")))
        return specs + incidents

    def _learning_queue_depth(self) -> int:
        return len(tuple(self.paths.learning_requests_queue_dir.glob("*.md")))


__all__ = ["DirectControlMutations"]
