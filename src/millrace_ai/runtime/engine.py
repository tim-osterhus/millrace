"""Thin runtime loop for Millrace."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from millrace_ai.architecture import CompiledRunPlan, MaterializedGraphNodePlan
from millrace_ai.config import RuntimeConfig
from millrace_ai.contracts import (
    ClosureTargetState,
    MailboxCommandEnvelope,
    Plane,
    RecoveryCounters,
    RuntimeSnapshot,
    StageName,
    StageResultEnvelope,
    WatcherMode,
    WorkItemKind,
)
from millrace_ai.errors import QueueStateError
from millrace_ai.events import write_runtime_event
from millrace_ai.paths import WorkspacePaths, bootstrap_workspace, workspace_paths
from millrace_ai.queue_store import QueueClaim, QueueStore
from millrace_ai.router import RouterDecision
from millrace_ai.runners import RunnerRawResult, StageRunRequest
from millrace_ai.runtime.monitoring import NullRuntimeMonitorSink, RuntimeMonitorEvent, RuntimeMonitorSink
from millrace_ai.state_store import (
    ReconciliationSignal,
    load_recovery_counters,
    reset_forward_progress_counters,
    save_recovery_counters,
    save_snapshot,
    set_execution_status,
    set_learning_status,
    set_planning_status,
)
from millrace_ai.watchers import WatcherSession, WatchEvent

from . import (
    activation,
    completion_behavior,
    lifecycle,
    mailbox_intake,
    reconciliation,
    result_application,
    stage_requests,
    tick_cycle,
    watcher_intake,
)
from .snapshot_state import IDLE_STATUS_MARKER, idle_snapshot_update

StageRunner = Callable[[StageRunRequest], RunnerRawResult]


@dataclass(frozen=True, slots=True)
class RuntimeTickOutcome:
    """Outcome from one runtime tick."""

    stage: StageName
    stage_result: StageResultEnvelope
    stage_result_path: Path
    router_decision: RouterDecision
    snapshot: RuntimeSnapshot


class RuntimeEngine:
    """Orchestrates startup, reconciliation, queue intake, and one stage per tick."""

    def __init__(
        self,
        target: WorkspacePaths | Path | str,
        *,
        stage_runner: StageRunner,
        config_path: Path | str | None = None,
        mode_id: str | None = None,
        assets_root: Path | None = None,
        monitor: RuntimeMonitorSink | None = None,
    ) -> None:
        self.paths = target if isinstance(target, WorkspacePaths) else workspace_paths(target)
        bootstrap_source = Path(assets_root).expanduser().resolve() if assets_root is not None else None
        self.paths = bootstrap_workspace(self.paths, assets_root=bootstrap_source)
        self.stage_runner = stage_runner
        self.config_path = (
            Path(config_path)
            if config_path is not None
            else self.paths.runtime_root / "millrace.toml"
        )
        self.mode_id = mode_id
        self.monitor = monitor or NullRuntimeMonitorSink()
        # Compile from workspace-local deployed assets so request paths and mode sources stay aligned.
        self.assets_root = self.paths.runtime_root

        self.config: RuntimeConfig | None = None
        self.compiled_plan: CompiledRunPlan | None = None
        self.snapshot: RuntimeSnapshot | None = None
        self.counters: RecoveryCounters | None = None
        self._daemon_lock_session_id: str | None = None
        self._watcher_session: WatcherSession | None = None

    def __del__(self) -> None:  # pragma: no cover - GC timing is non-deterministic
        try:
            self.close()
        except Exception:
            return

    def close(self) -> None:
        """Release any runtime-owned resources held by this engine session."""

        lifecycle.close_engine(self)

    def startup(self) -> RuntimeSnapshot:
        """Load config, compile the active mode, and reconcile stale runtime state."""

        return lifecycle.startup_engine(self)

    def tick(self) -> RuntimeTickOutcome:
        """Run one deterministic runtime tick."""

        return tick_cycle.run_tick(self)

    def _drain_mailbox(self) -> None:
        mailbox_intake.drain_mailbox(self)

    def _rebuild_watcher_session(self) -> None:
        watcher_intake.rebuild_watcher_session(self)

    def _close_watcher_session(self) -> None:
        watcher_intake.close_watcher_session(self)

    def _watcher_mode_value(self) -> WatcherMode:
        return watcher_intake.watcher_mode_value(self)

    def _consume_watcher_events(self) -> None:
        watcher_intake.consume_watcher_events(self)

    def _handle_watch_event(self, event: WatchEvent) -> None:
        watcher_intake.handle_watch_event(self, event)

    def _normalize_idea_watch_event(self, idea_path: Path) -> None:
        watcher_intake.normalize_idea_watch_event(self, idea_path)

    @staticmethod
    def _safe_spec_id_from_idea_path(path: Path) -> str:
        return watcher_intake.safe_spec_id_from_idea_path(path)

    @staticmethod
    def _derive_idea_title_summary(content: str, *, fallback: str) -> tuple[str, str]:
        return watcher_intake.derive_idea_title_summary(content, fallback=fallback)

    def _refresh_runtime_queue_depths(self, *, process_running: bool | None = None) -> None:
        reconciliation.refresh_runtime_queue_depths(self, process_running=process_running)

    def _run_reconciliation_if_needed(self) -> tuple[ReconciliationSignal, ...]:
        return reconciliation.run_reconciliation_if_needed(self)

    def _status_marker_for_reconciliation(self, path: Path) -> str:
        return reconciliation.status_marker_for_reconciliation(path)

    def _handle_mailbox_command(
        self,
        envelope: MailboxCommandEnvelope,
    ) -> None:  # pragma: no cover - thin integration path
        mailbox_intake.handle_mailbox_command(self, envelope)

    def _reload_config_from_mailbox(self) -> None:
        mailbox_intake.reload_config_from_mailbox(self)

    def _enqueue_task_from_mailbox(self, envelope: MailboxCommandEnvelope) -> None:
        mailbox_intake.enqueue_task_from_mailbox(self, envelope)

    def _enqueue_spec_from_mailbox(self, envelope: MailboxCommandEnvelope) -> None:
        mailbox_intake.enqueue_spec_from_mailbox(self, envelope)

    def _enqueue_idea_from_mailbox(self, envelope: MailboxCommandEnvelope) -> None:
        mailbox_intake.enqueue_idea_from_mailbox(self, envelope)

    def _claim_next_work_item(self) -> None:
        activation.claim_next_work_item(self)

    def _maybe_activate_completion_stage(self) -> ClosureTargetState | None:
        return completion_behavior.maybe_activate_completion_stage(self)

    def _active_closure_target(self) -> ClosureTargetState | None:
        return completion_behavior.active_closure_target(self)

    def _is_completion_stage_active(self) -> bool:
        assert self.snapshot is not None
        assert self.compiled_plan is not None
        completion = self.compiled_plan.planning_graph.compiled_completion_entry
        if completion is None:
            return False
        if self.snapshot.active_plane is None or self.snapshot.active_stage is None:
            return False
        if self.snapshot.active_plane is not completion.plane:
            return False
        active_node_id = self.snapshot.active_node_id or self.snapshot.active_stage.value
        if active_node_id != completion.node_id:
            return False
        return self.snapshot.active_work_item_kind is None and self.snapshot.active_work_item_id is None

    def _activate_claim(self, claim: QueueClaim) -> None:
        activation.activate_claim(self, claim)

    def _apply_reconciliation_signals(
        self,
        snapshot: RuntimeSnapshot,
        counters: RecoveryCounters,
        signals: tuple[ReconciliationSignal, ...],
    ) -> RuntimeSnapshot:
        return reconciliation.apply_reconciliation_signals(self, snapshot, counters, signals)

    def _set_recovery_counters(
        self,
        snapshot: RuntimeSnapshot,
        counters: RecoveryCounters,
        failure_class: str,
        stage: StageName,
    ) -> RuntimeSnapshot:
        return reconciliation.set_recovery_counters(self, snapshot, counters, failure_class, stage)

    def _route_stage_result(self, stage_result: StageResultEnvelope) -> RouterDecision:
        return result_application.route_stage_result(self, stage_result)

    def _apply_router_decision(self, decision: RouterDecision, stage_result: StageResultEnvelope) -> None:
        result_application.apply_router_decision(self, decision, stage_result)

    def _increment_route_counters(
        self,
        snapshot: RuntimeSnapshot,
        decision: RouterDecision,
        stage_result: StageResultEnvelope,
    ) -> RuntimeSnapshot:
        return result_application.increment_route_counters(self, snapshot, decision, stage_result)

    def _increment_counter_field(
        self,
        snapshot: RuntimeSnapshot,
        counters: RecoveryCounters,
        *,
        failure_class: str,
        work_item_kind: WorkItemKind,
        work_item_id: str,
        field: str,
    ) -> RuntimeSnapshot:
        return result_application.increment_counter_field(
            self,
            snapshot,
            counters,
            failure_class=failure_class,
            work_item_kind=work_item_kind,
            work_item_id=work_item_id,
            field=field,
        )

    def _mark_active_work_item_complete(self, stage_result: StageResultEnvelope) -> None:
        result_application.mark_active_work_item_complete(self, stage_result)

    def _mark_active_work_item_blocked(self, stage_result: StageResultEnvelope) -> None:
        result_application.mark_active_work_item_blocked(self, stage_result)

    def _mark_active_work_item_blocked_with_recovery(
        self,
        stage_result: StageResultEnvelope,
        *,
        reason: str,
    ) -> None:
        result_application.mark_active_work_item_blocked_with_recovery(
            self,
            stage_result,
            reason=reason,
        )

    def _enqueue_handoff_incident(
        self,
        *,
        decision: RouterDecision,
        stage_result: StageResultEnvelope,
    ) -> Path:
        return result_application.enqueue_handoff_incident(
            self,
            decision=decision,
            stage_result=stage_result,
        )

    def _write_stage_result(
        self,
        request: StageRunRequest,
        stage_result: StageResultEnvelope,
    ) -> Path:
        return result_application.write_stage_result(self, request, stage_result)

    def _write_plane_status(self, stage_result: StageResultEnvelope) -> None:
        result_application.write_plane_status(self, stage_result)

    def _build_stage_run_request(self, stage_plan: MaterializedGraphNodePlan) -> StageRunRequest:
        return stage_requests.build_stage_run_request(self, stage_plan)

    def _build_closure_target_stage_run_request(
        self,
        stage_plan: MaterializedGraphNodePlan,
        target_state: ClosureTargetState,
    ) -> StageRunRequest:
        return stage_requests.build_closure_target_stage_run_request(self, stage_plan, target_state)

    def _stage_plan_for(
        self,
        plane: Plane,
        stage: StageName,
        *,
        node_id: str | None = None,
    ) -> MaterializedGraphNodePlan:
        return stage_requests.stage_plan_for(self, plane, stage, node_id=node_id)

    def _entry_stage_for_kind(self, work_item_kind: WorkItemKind) -> StageName:
        return activation.entry_stage_for_kind(work_item_kind)

    def _idle_stage_for_no_work(self) -> StageName:
        return stage_requests.idle_stage_for_no_work()

    def _idle_tick_outcome(self, *, reason: str) -> RuntimeTickOutcome:
        return stage_requests.idle_tick_outcome(self, reason=reason)

    def _active_work_item_path(
        self,
        work_item_kind: WorkItemKind | None,
        work_item_id: str | None,
    ) -> Path | None:
        return stage_requests.active_work_item_path(self, work_item_kind, work_item_id)

    def _clear_stale_state(self, *, reason: str = "runtime stale-state clear") -> None:
        queue = QueueStore(self.paths)
        self._requeue_all_active_items(queue, reason=reason)
        self._reset_runtime_to_idle(
            process_running=True,
            clear_stop_requested=True,
            clear_paused=True,
        )
        save_recovery_counters(self.paths, RecoveryCounters())
        self.counters = load_recovery_counters(self.paths)

    def _retry_active(self, *, reason: str, scope: Plane | None = None) -> None:
        assert self.snapshot is not None
        if self.snapshot.active_work_item_kind is None or self.snapshot.active_work_item_id is None:
            return
        if scope is not None and self.snapshot.active_plane is not scope:
            write_runtime_event(
                self.paths,
                event_type="retry_active_skipped",
                data={
                    "requested_scope": scope.value,
                    "active_plane": self.snapshot.active_plane.value if self.snapshot.active_plane else None,
                    "work_item_kind": (
                        self.snapshot.active_work_item_kind.value
                        if self.snapshot.active_work_item_kind
                        else None
                    ),
                    "work_item_id": self.snapshot.active_work_item_id,
                },
            )
            return

        queue = QueueStore(self.paths)
        work_item_kind = self.snapshot.active_work_item_kind
        work_item_id = self.snapshot.active_work_item_id
        try:
            self._requeue_active_item(
                queue,
                work_item_kind=work_item_kind,
                work_item_id=work_item_id,
                reason=reason,
            )
        except QueueStateError:
            return

        self._reset_runtime_to_idle(
            process_running=True,
            clear_stop_requested=False,
            clear_paused=False,
        )
        reset_forward_progress_counters(
            self.paths,
            work_item_kind=work_item_kind,
            work_item_id=work_item_id,
        )
        self.counters = load_recovery_counters(self.paths)

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
        *,
        process_running: bool,
        clear_stop_requested: bool,
        clear_paused: bool,
    ) -> None:
        assert self.snapshot is not None
        update = idle_snapshot_update(
            now=self._now(),
            process_running=process_running,
            queue_depth_execution=self._execution_queue_depth(),
            queue_depth_planning=self._planning_queue_depth(),
            queue_depth_learning=self._learning_queue_depth(),
            clear_stop_requested=clear_stop_requested,
            clear_paused=clear_paused,
        )
        self.snapshot = self.snapshot.model_copy(update=update)
        save_snapshot(self.paths, self.snapshot)
        set_execution_status(self.paths, IDLE_STATUS_MARKER)
        set_planning_status(self.paths, IDLE_STATUS_MARKER)
        set_learning_status(self.paths, IDLE_STATUS_MARKER)

    def _mark_active_stage_running(
        self,
        *,
        plane: Plane,
        stage: StageName,
        running_status_marker: str,
    ) -> None:
        assert self.snapshot is not None
        marker = (
            running_status_marker
            if running_status_marker.startswith("### ")
            else f"### {running_status_marker}"
        )
        if plane is Plane.EXECUTION:
            set_execution_status(self.paths, marker)
            self.snapshot = self.snapshot.model_copy(
                update={"execution_status_marker": marker, "updated_at": self._now()}
            )
        elif plane is Plane.LEARNING:
            set_learning_status(self.paths, marker)
            self.snapshot = self.snapshot.model_copy(
                update={"learning_status_marker": marker, "updated_at": self._now()}
            )
        else:
            set_planning_status(self.paths, marker)
            self.snapshot = self.snapshot.model_copy(
                update={"planning_status_marker": marker, "updated_at": self._now()}
            )
        save_snapshot(self.paths, self.snapshot)

    @staticmethod
    def _mailbox_reason(envelope: MailboxCommandEnvelope, *, default: str) -> str:
        return mailbox_intake.mailbox_reason(envelope, default=default)

    @staticmethod
    def _mailbox_retry_scope(envelope: MailboxCommandEnvelope) -> Plane | None:
        return mailbox_intake.mailbox_retry_scope(envelope)

    def _execution_queue_depth(self) -> int:
        return stage_requests.execution_queue_depth(self)

    def _planning_queue_depth(self) -> int:
        return stage_requests.planning_queue_depth(self)

    def _learning_queue_depth(self) -> int:
        return stage_requests.learning_queue_depth(self)

    def _runner_failure_result(
        self,
        request: StageRunRequest,
        *,
        failure_class: str,
        error: str,
    ) -> RunnerRawResult:
        return stage_requests.runner_failure_result(
            request,
            failure_class=failure_class,
            error=error,
        )

    def _requires_daemon_ownership_lock(self) -> bool:
        return lifecycle.requires_daemon_ownership_lock(self)

    def _acquire_daemon_ownership_lock(self) -> bool:
        return lifecycle.acquire_daemon_ownership_lock(self)

    def _release_daemon_ownership_lock(self, *, force: bool) -> bool:
        return lifecycle.release_daemon_ownership_lock(self, force=force)

    def _new_run_id(self) -> str:
        return stage_requests.new_run_id()

    def _new_request_id(self) -> str:
        return stage_requests.new_request_id()

    def _now(self) -> datetime:
        return stage_requests.now()

    def _emit_monitor_event(self, event_type: str, **payload: object) -> None:
        self.monitor.emit(
            RuntimeMonitorEvent(
                event_type=event_type,
                occurred_at=self._now(),
                payload=payload,
            )
        )


__all__ = ["RuntimeEngine", "RuntimeTickOutcome"]
