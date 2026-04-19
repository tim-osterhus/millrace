"""Thin runtime loop for Millrace."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable
from uuid import uuid4

from millrace_ai.compiler import compile_and_persist_workspace_plan
from millrace_ai.config import RuntimeConfig, fingerprint_runtime_config, load_runtime_config
from millrace_ai.contracts import (
    ClosureTargetState,
    FrozenRunPlan,
    FrozenStagePlan,
    MailboxCommandEnvelope,
    Plane,
    RecoveryCounters,
    RuntimeSnapshot,
    StageName,
    StageResultEnvelope,
    WatcherMode,
    WorkItemKind,
)
from millrace_ai.errors import (
    QueueStateError,
    RuntimeLifecycleError,
    WorkspaceStateError,
)
from millrace_ai.events import write_runtime_event
from millrace_ai.paths import WorkspacePaths, bootstrap_workspace, workspace_paths
from millrace_ai.queue_store import QueueClaim, QueueStore
from millrace_ai.router import RouterDecision
from millrace_ai.runners import RunnerRawResult, StageRunRequest, normalize_stage_result
from millrace_ai.runtime_lock import (
    RuntimeOwnershipLockError,
    acquire_runtime_ownership_lock,
    release_runtime_ownership_lock,
)
from millrace_ai.state_store import (
    ReconciliationSignal,
    load_recovery_counters,
    load_snapshot,
    reset_forward_progress_counters,
    save_recovery_counters,
    save_snapshot,
    set_execution_status,
    set_planning_status,
)
from millrace_ai.watchers import WatcherSession, WatchEvent

from . import (
    activation,
    completion_behavior,
    mailbox_intake,
    reconciliation,
    result_application,
    stage_requests,
    watcher_intake,
)
from .error_recovery import schedule_post_stage_exception_recovery
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
        # Compile from workspace-local deployed assets so request paths and mode sources stay aligned.
        self.assets_root = self.paths.runtime_root

        self.config: RuntimeConfig | None = None
        self.compiled_plan: FrozenRunPlan | None = None
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

        self._close_watcher_session()
        self._release_daemon_ownership_lock(force=False)

    def startup(self) -> RuntimeSnapshot:
        """Load config, compile the active mode, and reconcile stale runtime state."""

        lock_acquired = False
        try:
            self.config = load_runtime_config(self.config_path)
            if self._requires_daemon_ownership_lock():
                lock_acquired = self._acquire_daemon_ownership_lock()
            self._rebuild_watcher_session()

            compile_outcome = compile_and_persist_workspace_plan(
                self.paths,
                config=self.config,
                requested_mode_id=self.mode_id,
                assets_root=self.assets_root,
            )
            compiled_plan = compile_outcome.active_plan
            if compiled_plan is None:
                errors = ", ".join(compile_outcome.diagnostics.errors) or "compile failed"
                raise RuntimeLifecycleError(errors)

            self.compiled_plan = compiled_plan

            self.snapshot = load_snapshot(self.paths)
            self.counters = load_recovery_counters(self.paths)
            self._run_reconciliation_if_needed()

            assert self.snapshot is not None
            snapshot = self.snapshot.model_copy(
                update={
                    "runtime_mode": self.config.runtime.run_style,
                    "process_running": True,
                    "active_mode_id": compiled_plan.mode_id,
                    "execution_loop_id": compiled_plan.execution_loop_id,
                    "planning_loop_id": compiled_plan.planning_loop_id,
                    "compiled_plan_id": compiled_plan.compiled_plan_id,
                    "compiled_plan_path": str((self.paths.state_dir / "compiled_plan.json").relative_to(self.paths.root)),
                    "queue_depth_execution": self._execution_queue_depth(),
                    "queue_depth_planning": self._planning_queue_depth(),
                    "config_version": fingerprint_runtime_config(self.config),
                    "watcher_mode": self._watcher_mode_value(),
                    "last_reload_outcome": None,
                    "last_reload_error": None,
                    "updated_at": self._now(),
                }
            )

            self.snapshot = snapshot
            save_snapshot(self.paths, snapshot)
            write_runtime_event(
                self.paths,
                event_type="runtime_started",
                data={
                    "mode_id": snapshot.active_mode_id,
                    "compiled_plan_id": snapshot.compiled_plan_id,
                    "process_running": snapshot.process_running,
                },
            )
            return snapshot
        except Exception:
            self._close_watcher_session()
            if lock_acquired:
                self._release_daemon_ownership_lock(force=True)
            raise

    def tick(self) -> RuntimeTickOutcome:
        """Run one deterministic runtime tick."""

        if self.snapshot is None or self.counters is None or self.compiled_plan is None:
            self.startup()
        assert self.snapshot is not None
        assert self.counters is not None
        assert self.compiled_plan is not None

        # Deterministic tick order: mailbox/control intake, reconciliation, then stage execution.
        self._drain_mailbox()
        self._consume_watcher_events()
        self._refresh_runtime_queue_depths()

        if self.snapshot.stop_requested:
            self._reset_runtime_to_idle(
                process_running=False,
                clear_stop_requested=True,
                clear_paused=True,
            )
            self._close_watcher_session()
            self._release_daemon_ownership_lock(force=False)
            write_runtime_event(self.paths, event_type="runtime_tick_stopped")
            return self._idle_tick_outcome(reason="stop_requested")

        if self.snapshot.paused:
            save_snapshot(self.paths, self.snapshot)
            write_runtime_event(self.paths, event_type="runtime_tick_paused")
            return self._idle_tick_outcome(reason="paused")

        self._run_reconciliation_if_needed()
        self._refresh_runtime_queue_depths(process_running=True)

        if self.snapshot.active_stage is None:
            self._claim_next_work_item()

        if self.snapshot.active_stage is None:
            self._maybe_activate_completion_stage()

        if (
            self.snapshot.active_stage is not None
            and self.snapshot.active_plane is not None
            and (
                self.snapshot.active_work_item_kind is None
                or self.snapshot.active_work_item_id is None
            )
            and not self._is_completion_stage_active()
        ):
            write_runtime_event(
                self.paths,
                event_type="runtime_tick_invalid_active_state",
                data={"reason": "missing_active_work_item_identity"},
            )
            self._clear_stale_state()
            save_snapshot(self.paths, self.snapshot)
            return self._idle_tick_outcome(reason="missing_active_work_item_identity")

        if self.snapshot.active_stage is None or self.snapshot.active_plane is None:
            save_snapshot(self.paths, self.snapshot)
            write_runtime_event(self.paths, event_type="runtime_tick_idle")
            return self._idle_tick_outcome(reason="no_work")

        stage_plan = self._stage_plan_for(self.snapshot.active_plane, self.snapshot.active_stage)
        if self._is_completion_stage_active():
            closure_target = self._active_closure_target()
            if closure_target is None:
                raise WorkspaceStateError("completion stage is active without an open closure target")
            request = self._build_closure_target_stage_run_request(stage_plan, closure_target)
        else:
            request = self._build_stage_run_request(stage_plan)
        write_runtime_event(
            self.paths,
            event_type="stage_started",
            data={
                "request_id": request.request_id,
                "stage": request.stage.value,
                "plane": request.plane.value,
                "run_id": request.run_id,
                "work_item_kind": request.active_work_item_kind.value if request.active_work_item_kind else None,
                "work_item_id": request.active_work_item_id,
                "troubleshoot_report_path": request.preferred_troubleshoot_report_path,
            },
        )

        try:
            raw_result = self.stage_runner(request)
        except Exception as exc:  # pragma: no cover - defensive path
            raw_result = self._runner_failure_result(request, failure_class="runner_error", error=str(exc))

        stage_result = normalize_stage_result(request, raw_result)
        stage_result_path: Path | None = None
        router_decision: RouterDecision | None = None
        try:
            stage_result_path = self._write_stage_result(request, stage_result)
            router_decision = self._route_stage_result(stage_result)
            self._write_plane_status(stage_result)
            self._apply_router_decision(router_decision, stage_result)
        except Exception as exc:
            recovery_decision = schedule_post_stage_exception_recovery(
                self,
                stage_result=stage_result,
                error=exc,
                router_decision=router_decision,
                stage_result_path=stage_result_path,
            )
            return RuntimeTickOutcome(
                stage=stage_result.stage,
                stage_result=stage_result,
                stage_result_path=stage_result_path
                or (self.paths.logs_dir / f"{request.request_id}.stage_result.unavailable.json"),
                router_decision=recovery_decision,
                snapshot=self.snapshot,
            )

        assert stage_result_path is not None
        assert router_decision is not None
        self.snapshot = self.snapshot.model_copy(
            update={
                "last_terminal_result": stage_result.terminal_result,
                "last_stage_result_path": str(stage_result_path.relative_to(self.paths.root)),
                "queue_depth_execution": self._execution_queue_depth(),
                "queue_depth_planning": self._planning_queue_depth(),
                "updated_at": self._now(),
            }
        )
        save_snapshot(self.paths, self.snapshot)
        write_runtime_event(
            self.paths,
            event_type="stage_completed",
            data={
                "request_id": request.request_id,
                "stage": stage_result.stage.value,
                "plane": stage_result.plane.value,
                "run_id": request.run_id,
                "work_item_kind": stage_result.work_item_kind.value,
                "work_item_id": stage_result.work_item_id,
                "terminal_result": stage_result.terminal_result.value,
                "failure_class": stage_result.metadata.get("failure_class"),
                "troubleshoot_report_path": (
                    stage_result.report_artifact or request.preferred_troubleshoot_report_path
                ),
            },
        )
        write_runtime_event(
            self.paths,
            event_type="router_decision",
            data={
                "action": router_decision.action.value,
                "plane": stage_result.plane.value,
                "run_id": request.run_id,
                "work_item_kind": stage_result.work_item_kind.value,
                "work_item_id": stage_result.work_item_id,
                "stage": stage_result.stage.value,
                "terminal_result": stage_result.terminal_result.value,
                "failure_class": stage_result.metadata.get("failure_class"),
                "troubleshoot_report_path": (
                    stage_result.report_artifact or request.preferred_troubleshoot_report_path
                ),
                "next_stage": router_decision.next_stage.value if router_decision.next_stage else None,
                "reason": router_decision.reason,
            },
        )

        return RuntimeTickOutcome(
            stage=stage_result.stage,
            stage_result=stage_result,
            stage_result_path=stage_result_path,
            router_decision=router_decision,
            snapshot=self.snapshot,
        )

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
        completion = self.compiled_plan.completion_behavior
        if completion is None:
            return False
        if self.snapshot.active_plane is None or self.snapshot.active_stage is None:
            return False
        if self.snapshot.active_stage != completion.stage:
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

    def _build_stage_run_request(self, stage_plan: FrozenStagePlan) -> StageRunRequest:
        return stage_requests.build_stage_run_request(self, stage_plan)

    def _build_closure_target_stage_run_request(
        self,
        stage_plan: FrozenStagePlan,
        target_state: ClosureTargetState,
    ) -> StageRunRequest:
        return stage_requests.build_closure_target_stage_run_request(self, stage_plan, target_state)

    def _stage_plan_for(self, plane: Plane, stage: StageName) -> FrozenStagePlan:
        return stage_requests.stage_plan_for(self, plane, stage)

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
            clear_stop_requested=clear_stop_requested,
            clear_paused=clear_paused,
        )
        self.snapshot = self.snapshot.model_copy(update=update)
        save_snapshot(self.paths, self.snapshot)
        set_execution_status(self.paths, IDLE_STATUS_MARKER)
        set_planning_status(self.paths, IDLE_STATUS_MARKER)

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
        return self.config is not None

    def _acquire_daemon_ownership_lock(self) -> bool:
        if self._daemon_lock_session_id is not None:
            return False

        session_id = uuid4().hex
        try:
            acquire_runtime_ownership_lock(self.paths, owner_session_id=session_id)
        except RuntimeOwnershipLockError as exc:
            write_runtime_event(
                self.paths,
                event_type="runtime_daemon_lock_denied",
                data={"reason": str(exc)},
            )
            raise RuntimeLifecycleError(str(exc)) from exc

        self._daemon_lock_session_id = session_id
        write_runtime_event(
            self.paths,
            event_type="runtime_daemon_lock_acquired",
            data={"session_id": session_id},
        )
        return True

    def _release_daemon_ownership_lock(self, *, force: bool) -> bool:
        session_id = self._daemon_lock_session_id
        if session_id is None and not force:
            return False
        released = release_runtime_ownership_lock(
            self.paths,
            owner_session_id=session_id,
            force=force,
        )
        if released:
            write_runtime_event(
                self.paths,
                event_type="runtime_daemon_lock_released",
                data={"session_id": session_id},
            )
        self._daemon_lock_session_id = None
        return released

    def _new_run_id(self) -> str:
        return stage_requests.new_run_id()

    def _new_request_id(self) -> str:
        return stage_requests.new_request_id()

    def _now(self) -> datetime:
        return stage_requests.now()


__all__ = ["RuntimeEngine", "RuntimeTickOutcome"]
