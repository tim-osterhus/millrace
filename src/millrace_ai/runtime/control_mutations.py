"""Direct control mutations that operate on offline workspace state."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Generic, TypeVar

from millrace_ai.compilation.persistence import load_existing_plan
from millrace_ai.compiler import compile_and_persist_workspace_plan
from millrace_ai.config import fingerprint_runtime_config, load_runtime_config
from millrace_ai.contracts import (
    ActiveRunState,
    MailboxAddIdeaPayload,
    MailboxArchiveBlockedTaskPayload,
    MailboxArchiveInvalidIncidentPayload,
    MailboxCancelWorkItemPayload,
    MailboxCommand,
    MailboxExecutionCapabilityApprovalPayload,
    MailboxIncidentInterventionPayload,
    MailboxRetargetTaskDependencyPayload,
    MailboxSupersedeTaskPayload,
    Plane,
    ProbeDocument,
    RecoveryCounters,
    ReloadOutcome,
    RuntimeSnapshot,
    SpecDocument,
    TaskDocument,
    WorkItemKind,
)
from millrace_ai.errors import QueueStateError, WorkspaceStateError
from millrace_ai.paths import WorkspacePaths
from millrace_ai.queue_store import QueueStore
from millrace_ai.runtime.active_runs import active_run_for_plane, snapshot_without_active_plane
from millrace_ai.runtime.approvals import (
    approve_execution_capability_request,
    deny_execution_capability_request,
)
from millrace_ai.runtime.compiled_plans import archive_compiled_plan, relative_plan_path
from millrace_ai.runtime.control_mailbox import ControlActionResultFactory
from millrace_ai.runtime.lanes import compiled_plan_fingerprint_for_runtime, ensure_snapshot_lanes
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
from millrace_ai.workspace.operator_interventions import OperatorInterventionResult
from millrace_ai.workspace.queue_lifecycle import requeue_active_work_item, requeue_all_active_work_items
from millrace_ai.workspace.work_inventory import queue_depths_by_plane

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
        self._save_queue_depth_snapshot(snapshot)
        return self._result_factory(
            action=MailboxCommand.ADD_TASK,
            mode="direct",
            applied=True,
            detail="task queued directly",
            artifact_path=destination,
        )

    def add_spec(self, snapshot: RuntimeSnapshot, *, document: SpecDocument) -> ResultT:
        destination = QueueStore(self.paths).enqueue_spec(document)
        self._save_queue_depth_snapshot(snapshot)
        return self._result_factory(
            action=MailboxCommand.ADD_SPEC,
            mode="direct",
            applied=True,
            detail="spec queued directly",
            artifact_path=destination,
        )

    def add_probe(self, snapshot: RuntimeSnapshot, *, document: ProbeDocument) -> ResultT:
        destination = QueueStore(self.paths).enqueue_probe(document)
        self._save_queue_depth_snapshot(snapshot)
        return self._result_factory(
            action=MailboxCommand.ADD_PROBE,
            mode="direct",
            applied=True,
            detail="probe queued directly",
            artifact_path=destination,
        )

    def add_idea(self, snapshot: RuntimeSnapshot, *, payload: MailboxAddIdeaPayload) -> ResultT:
        destination_dir = self.paths.root / "ideas" / "inbox"
        destination_dir.mkdir(parents=True, exist_ok=True)
        destination = destination_dir / payload.source_name
        if destination.exists():
            raise WorkspaceStateError(f"idea document already exists: {destination}")
        destination.write_text(payload.markdown, encoding="utf-8")
        self._save_queue_depth_snapshot(snapshot)
        return self._result_factory(
            action=MailboxCommand.ADD_IDEA,
            mode="direct",
            applied=True,
            detail="idea staged directly",
            artifact_path=destination,
        )

    def cancel_work_item(
        self,
        snapshot: RuntimeSnapshot,
        *,
        payload: MailboxCancelWorkItemPayload,
    ) -> ResultT:
        active_result = self._active_intervention_block(
            snapshot,
            action=MailboxCommand.CANCEL_WORK_ITEM,
        )
        if active_result is not None:
            return active_result
        result = QueueStore(self.paths).cancel_work_item(
            payload.work_item_id,
            work_item_family_id=payload.work_item_family_id,
            work_item_kind=payload.work_item_kind,
            reason=payload.reason,
            force=payload.force,
        )
        self._save_queue_depth_snapshot(snapshot)
        return self._intervention_result(MailboxCommand.CANCEL_WORK_ITEM, result)

    def archive_blocked_task(
        self,
        snapshot: RuntimeSnapshot,
        *,
        payload: MailboxArchiveBlockedTaskPayload,
    ) -> ResultT:
        active_result = self._active_intervention_block(
            snapshot,
            action=MailboxCommand.ARCHIVE_BLOCKED_TASK,
        )
        if active_result is not None:
            return active_result
        result = QueueStore(self.paths).archive_blocked_task(payload.task_id, reason=payload.reason)
        self._save_queue_depth_snapshot(snapshot)
        return self._intervention_result(MailboxCommand.ARCHIVE_BLOCKED_TASK, result)

    def supersede_task(
        self,
        snapshot: RuntimeSnapshot,
        *,
        payload: MailboxSupersedeTaskPayload,
    ) -> ResultT:
        active_result = self._active_intervention_block(
            snapshot,
            action=MailboxCommand.SUPERSEDE_TASK,
        )
        if active_result is not None:
            return active_result
        result = QueueStore(self.paths).supersede_task(
            payload.old_task_id,
            replacement_task_id=payload.replacement_task_id,
            reason=payload.reason,
            cascade=payload.cascade,
        )
        self._save_queue_depth_snapshot(snapshot)
        return self._intervention_result(MailboxCommand.SUPERSEDE_TASK, result)

    def retarget_task_dependency(
        self,
        snapshot: RuntimeSnapshot,
        *,
        payload: MailboxRetargetTaskDependencyPayload,
    ) -> ResultT:
        active_result = self._active_intervention_block(
            snapshot,
            action=MailboxCommand.RETARGET_TASK_DEPENDENCY,
        )
        if active_result is not None:
            return active_result
        result = QueueStore(self.paths).retarget_queued_task_dependency(
            payload.task_id,
            old_dependency_id=payload.old_dependency_id,
            new_dependency_id=payload.new_dependency_id,
            reason=payload.reason,
        )
        self._save_queue_depth_snapshot(snapshot)
        return self._intervention_result(MailboxCommand.RETARGET_TASK_DEPENDENCY, result)

    def resolve_incident(
        self,
        snapshot: RuntimeSnapshot,
        *,
        payload: MailboxIncidentInterventionPayload,
    ) -> ResultT:
        active_result = self._active_intervention_block(
            snapshot,
            action=MailboxCommand.RESOLVE_INCIDENT,
        )
        if active_result is not None:
            return active_result
        result = QueueStore(self.paths).resolve_incident_by_operator(
            payload.incident_id,
            reason=payload.reason,
        )
        self._save_queue_depth_snapshot(snapshot)
        return self._intervention_result(MailboxCommand.RESOLVE_INCIDENT, result)

    def cancel_incident(
        self,
        snapshot: RuntimeSnapshot,
        *,
        payload: MailboxIncidentInterventionPayload,
    ) -> ResultT:
        active_result = self._active_intervention_block(
            snapshot,
            action=MailboxCommand.CANCEL_INCIDENT,
        )
        if active_result is not None:
            return active_result
        result = QueueStore(self.paths).cancel_incident(payload.incident_id, reason=payload.reason)
        self._save_queue_depth_snapshot(snapshot)
        return self._intervention_result(MailboxCommand.CANCEL_INCIDENT, result)

    def archive_invalid_incident(
        self,
        snapshot: RuntimeSnapshot,
        *,
        payload: MailboxArchiveInvalidIncidentPayload,
    ) -> ResultT:
        active_result = self._active_intervention_block(
            snapshot,
            action=MailboxCommand.ARCHIVE_INVALID_INCIDENT,
        )
        if active_result is not None:
            return active_result
        result = QueueStore(self.paths).archive_invalid_incident_artifact(
            payload.filename,
            reason=payload.reason,
        )
        self._save_queue_depth_snapshot(snapshot)
        return self._intervention_result(MailboxCommand.ARCHIVE_INVALID_INCIDENT, result)

    def approve_execution_capability(
        self,
        snapshot: RuntimeSnapshot,
        *,
        payload: MailboxExecutionCapabilityApprovalPayload,
        actor: str = "operator",
    ) -> ResultT:
        approval = approve_execution_capability_request(
            self.paths,
            payload.approval_id,
            decided_by=actor,
            reason=payload.reason,
            now=self._now(),
        )
        save_snapshot(self.paths, snapshot.model_copy(update={"updated_at": self._now()}))
        return self._result_factory(
            action=MailboxCommand.APPROVE_EXECUTION_CAPABILITY,
            mode="direct",
            applied=True,
            detail=f"approved execution capability request {approval.approval_id}",
        )

    def deny_execution_capability(
        self,
        snapshot: RuntimeSnapshot,
        *,
        payload: MailboxExecutionCapabilityApprovalPayload,
        actor: str = "operator",
    ) -> ResultT:
        approval = deny_execution_capability_request(
            self.paths,
            payload.approval_id,
            decided_by=actor,
            reason=payload.reason,
            now=self._now(),
        )
        save_snapshot(self.paths, snapshot.model_copy(update={"updated_at": self._now()}))
        return self._result_factory(
            action=MailboxCommand.DENY_EXECUTION_CAPABILITY,
            mode="direct",
            applied=True,
            detail=f"denied execution capability request {approval.approval_id}",
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
        if active_run.work_item_family_id is None or active_run.work_item_id is None:
            return self._result_factory(
                action=MailboxCommand.RETRY_ACTIVE,
                mode="direct",
                applied=False,
                detail=f"active {active_run.plane.value} run is not a retryable work item",
            )

        work_item_family_id = active_run.work_item_family_id
        work_item_id = active_run.work_item_id

        try:
            self._requeue_active_item(
                work_item_family_id=work_item_family_id,
                work_item_kind=active_run.work_item_kind,
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
            work_item_family_id=work_item_family_id,
            work_item_kind=active_run.work_item_kind,
            work_item_id=work_item_id,
        )
        return self._result_factory(
            action=MailboxCommand.RETRY_ACTIVE,
            mode="direct",
            applied=True,
            detail=f"active {work_item_family_id} {work_item_id} requeued",
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
        requeued_count = self._requeue_all_active_items(reason=reason)
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
        try:
            reloaded_config = load_runtime_config(self.paths.runtime_root / "millrace.toml")
        except ValueError as exc:
            updated = snapshot.model_copy(
                update={
                    "last_reload_outcome": ReloadOutcome.FAILED_RETAINED_PREVIOUS_PLAN,
                    "last_reload_error": str(exc),
                    "updated_at": self._now(),
                }
            )
            save_snapshot(self.paths, updated)
            return self._result_factory(
                action=MailboxCommand.RELOAD_CONFIG,
                mode="direct",
                applied=False,
                detail=f"reload failed; retained previous plan: {exc}",
            )

        compile_outcome = compile_and_persist_workspace_plan(
            self.paths,
            config=reloaded_config,
            requested_mode_id=None,
            assets_root=self.paths.runtime_root,
            compile_if_needed=True,
            refuse_stale_last_known_good=True,
        )
        active_plan = compile_outcome.active_plan
        if active_plan is None or not compile_outcome.diagnostics.ok:
            errors = ", ".join(compile_outcome.diagnostics.errors) or "compile failed"
            updated = snapshot.model_copy(
                update={
                    "last_reload_outcome": ReloadOutcome.FAILED_RETAINED_PREVIOUS_PLAN,
                    "last_reload_error": errors,
                    "updated_at": self._now(),
                }
            )
            save_snapshot(self.paths, updated)
            return self._result_factory(
                action=MailboxCommand.RELOAD_CONFIG,
                mode="direct",
                applied=False,
                detail=f"reload failed; retained previous plan: {errors}",
            )

        archive_path = archive_compiled_plan(self.paths, active_plan)
        plan_fingerprint = compiled_plan_fingerprint_for_runtime(active_plan)
        if snapshot.active_runs_by_plane:
            updated = snapshot.model_copy(
                update={
                    "runtime_mode": reloaded_config.runtime.run_style,
                    "pending_compiled_plan_id": active_plan.compiled_plan_id,
                    "pending_compiled_plan_path": relative_plan_path(self.paths, archive_path),
                    "pending_compiled_plan_fingerprint": plan_fingerprint,
                    "config_version": fingerprint_runtime_config(reloaded_config),
                    "last_reload_outcome": ReloadOutcome.APPLIED,
                    "last_reload_error": None,
                    "updated_at": self._now(),
                }
            )
            save_snapshot(self.paths, updated)
            return self._result_factory(
                action=MailboxCommand.RELOAD_CONFIG,
                mode="direct",
                applied=True,
                detail=f"reload compiled pending plan {active_plan.compiled_plan_id}",
            )

        updated = ensure_snapshot_lanes(snapshot, active_plan).model_copy(
            update={
                **self._queue_depth_update(compiled_plan=active_plan),
                "runtime_mode": reloaded_config.runtime.run_style,
                "active_mode_id": active_plan.mode_id,
                "execution_loop_id": active_plan.execution_loop_id,
                "planning_loop_id": active_plan.planning_loop_id,
                "learning_loop_id": active_plan.learning_loop_id,
                "loop_ids_by_plane": active_plan.loop_ids_by_plane,
                "compiled_plan_id": active_plan.compiled_plan_id,
                "compiled_plan_fingerprint": plan_fingerprint,
                "compiled_plan_path": str((self.paths.state_dir / "compiled_plan.json").relative_to(self.paths.root)),
                "pending_compiled_plan_id": None,
                "pending_compiled_plan_path": None,
                "pending_compiled_plan_fingerprint": None,
                "config_version": fingerprint_runtime_config(reloaded_config),
                "last_reload_outcome": ReloadOutcome.APPLIED,
                "last_reload_error": None,
                "updated_at": self._now(),
            }
        )
        save_snapshot(self.paths, updated)
        return self._result_factory(
            action=MailboxCommand.RELOAD_CONFIG,
            mode="direct",
            applied=True,
            detail=f"reload applied plan {active_plan.compiled_plan_id}",
        )

    def _requeue_all_active_items(self, *, reason: str) -> int:
        return requeue_all_active_work_items(
            self.paths,
            reason=reason,
            work_item_families=self._work_item_families_for_inventory(),
        )

    def _requeue_active_item(
        self,
        *,
        work_item_family_id: str,
        work_item_kind: WorkItemKind | None,
        work_item_id: str,
        reason: str,
    ) -> None:
        requeue_active_work_item(
            self.paths,
            work_item_family_id=work_item_family_id,
            work_item_kind=work_item_kind,
            work_item_id=work_item_id,
            reason=reason,
            work_item_families=self._work_item_families_for_inventory(),
        )

    def _reset_runtime_to_idle(
        self,
        snapshot: RuntimeSnapshot,
        *,
        process_running: bool,
        clear_stop_requested: bool,
        clear_paused: bool,
    ) -> None:
        queue_depths = self._queue_depths()
        updated = snapshot.model_copy(
            update={
                **idle_snapshot_update(
                    now=self._now(),
                    process_running=process_running,
                    queue_depth_execution=queue_depths[Plane.EXECUTION],
                    queue_depth_planning=queue_depths[Plane.PLANNING],
                    queue_depth_learning=queue_depths[Plane.LEARNING],
                    clear_stop_requested=clear_stop_requested,
                    clear_paused=clear_paused,
                )
            }
        )

        save_snapshot(self.paths, updated)
        set_execution_status(self.paths, IDLE_STATUS_MARKER)
        set_planning_status(self.paths, IDLE_STATUS_MARKER)
        set_learning_status(self.paths, IDLE_STATUS_MARKER)

    def _active_intervention_block(
        self,
        snapshot: RuntimeSnapshot,
        *,
        action: MailboxCommand,
    ) -> ResultT | None:
        if snapshot.active_runs_by_plane or snapshot.active_stage is not None:
            active_planes = ", ".join(plane.value for plane in snapshot.active_runs_by_plane) or "legacy"
            return self._result_factory(
                action=action,
                mode="direct",
                applied=False,
                detail=f"active runtime stage prevents operator intervention; active_planes={active_planes}",
            )
        return None

    def _save_queue_depth_snapshot(self, snapshot: RuntimeSnapshot) -> None:
        queue_depths = self._queue_depths()
        save_snapshot(
            self.paths,
            snapshot.model_copy(
                update={
                    "queue_depth_execution": queue_depths[Plane.EXECUTION],
                    "queue_depth_planning": queue_depths[Plane.PLANNING],
                    "queue_depth_learning": queue_depths[Plane.LEARNING],
                    "queue_depths_by_plane": queue_depths,
                    "updated_at": self._now(),
                }
            ),
        )

    def _intervention_result(
        self,
        action: MailboxCommand,
        result: OperatorInterventionResult,
    ) -> ResultT:
        detail = f"{result.event_type}: {result.work_item_family_id} {result.work_item_id}"
        if result.replacement_work_item_id is not None:
            detail += f" replacement={result.replacement_work_item_id}"
        if result.affected_dependents:
            detail += f" affected_dependents={','.join(result.affected_dependents)}"
        return self._result_factory(
            action=action,
            mode="direct",
            applied=True,
            detail=detail,
            artifact_path=result.destination_path,
        )

    def _compiled_plan_for_inventory(self):
        return load_existing_plan(self.paths.state_dir / "compiled_plan.json")

    def _work_item_families_for_inventory(self):
        compiled_plan = self._compiled_plan_for_inventory()
        if compiled_plan is None:
            return None
        return tuple(compiled_plan.work_item_families_by_id.values())

    def _queue_depths(self, *, compiled_plan=None) -> dict[Plane, int]:
        if compiled_plan is None:
            compiled_plan = self._compiled_plan_for_inventory()
        return queue_depths_by_plane(self.paths, compiled_plan=compiled_plan)

    def _queue_depth_update(self, *, compiled_plan=None) -> dict[str, object]:
        queue_depths = self._queue_depths(compiled_plan=compiled_plan)
        return {
            "queue_depth_execution": queue_depths[Plane.EXECUTION],
            "queue_depth_planning": queue_depths[Plane.PLANNING],
            "queue_depth_learning": queue_depths[Plane.LEARNING],
            "queue_depths_by_plane": queue_depths,
        }

    def _execution_queue_depth(self) -> int:
        return self._queue_depths()[Plane.EXECUTION]

    def _planning_queue_depth(self) -> int:
        return self._queue_depths()[Plane.PLANNING]

    def _learning_queue_depth(self) -> int:
        return self._queue_depths()[Plane.LEARNING]


__all__ = ["DirectControlMutations"]
