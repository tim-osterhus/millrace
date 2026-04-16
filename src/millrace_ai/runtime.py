"""Thin runtime loop for Millrace."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from uuid import uuid4

from millrace_ai.compiler import compile_and_persist_workspace_plan
from millrace_ai.config import RuntimeConfig, fingerprint_runtime_config, load_runtime_config
from millrace_ai.contracts import (
    ExecutionStageName,
    ExecutionTerminalResult,
    FrozenRunPlan,
    FrozenStagePlan,
    IncidentDecision,
    IncidentDocument,
    IncidentSeverity,
    MailboxAddIdeaPayload,
    MailboxAddSpecPayload,
    MailboxAddTaskPayload,
    MailboxCommandEnvelope,
    Plane,
    PlanningStageName,
    RecoveryCounterEntry,
    RecoveryCounters,
    ReloadOutcome,
    ResultClass,
    RuntimeMode,
    RuntimeSnapshot,
    SpecDocument,
    StageName,
    StageResultEnvelope,
    WatcherMode,
    WorkItemKind,
)
from millrace_ai.errors import (
    ControlRoutingError,
    QueueStateError,
    RuntimeLifecycleError,
    WorkspaceStateError,
)
from millrace_ai.events import write_runtime_event
from millrace_ai.mailbox import drain_incoming_mailbox_commands
from millrace_ai.paths import WorkspacePaths, bootstrap_workspace, workspace_paths
from millrace_ai.queue_store import QueueClaim, QueueStore
from millrace_ai.router import RouterAction, RouterDecision, next_execution_step, next_planning_step
from millrace_ai.runner import RunnerRawResult, StageRunRequest, normalize_stage_result
from millrace_ai.runtime_lock import (
    RuntimeOwnershipLockError,
    acquire_runtime_ownership_lock,
    release_runtime_ownership_lock,
)
from millrace_ai.state_store import (
    ReconciliationSignal,
    collect_reconciliation_signals,
    load_recovery_counters,
    load_snapshot,
    reset_forward_progress_counters,
    save_recovery_counters,
    save_snapshot,
    set_execution_status,
    set_planning_status,
)
from millrace_ai.watchers import WatcherSession, WatchEvent, build_watcher_session

StageRunner = Callable[[StageRunRequest], RunnerRawResult]
_STATUS_IDLE = "### IDLE"
_INVALID_RECONCILIATION_MARKER = "### INVALID_STATUS_MARKER"
_IDEA_ID_SANITIZER = re.compile(r"[^A-Za-z0-9._-]+")


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
        self._close_watcher_session()
        if getattr(self, "_daemon_lock_session_id", None) is None:
            return
        try:
            self._release_daemon_ownership_lock(force=False)
        except Exception:
            return

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
            self.snapshot = self.snapshot.model_copy(
                update={
                    "process_running": False,
                    "updated_at": self._now(),
                }
            )
            save_snapshot(self.paths, self.snapshot)
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

        if (
            self.snapshot.active_stage is not None
            and self.snapshot.active_plane is not None
            and (
                self.snapshot.active_work_item_kind is None
                or self.snapshot.active_work_item_id is None
            )
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
        stage_result_path = self._write_stage_result(request, stage_result)
        router_decision = self._route_stage_result(stage_result)
        self._write_plane_status(stage_result)
        self._apply_router_decision(router_decision, stage_result)
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
        drain_incoming_mailbox_commands(self.paths, handler=self._handle_mailbox_command)

    def _rebuild_watcher_session(self) -> None:
        assert self.config is not None
        self._close_watcher_session()
        self._watcher_session = build_watcher_session(
            self.paths,
            config=self.config,
            config_path=self.config_path,
        )

    def _close_watcher_session(self) -> None:
        if self._watcher_session is None:
            return
        self._watcher_session.close()
        self._watcher_session = None

    def _watcher_mode_value(self) -> WatcherMode:
        if self._watcher_session is None:
            if self.snapshot is not None:
                return self.snapshot.watcher_mode
            return WatcherMode.OFF
        return self._watcher_session.mode

    def _consume_watcher_events(self) -> None:
        if self._watcher_session is None:
            return
        events = self._watcher_session.poll_once(now=self._now())
        if not events:
            return

        for event in events:
            self._handle_watch_event(event)

        assert self.snapshot is not None
        self.snapshot = self.snapshot.model_copy(update={"updated_at": self._now()})
        save_snapshot(self.paths, self.snapshot)
        write_runtime_event(
            self.paths,
            event_type="watcher_events_consumed",
            data={"count": len(events)},
        )

    def _handle_watch_event(self, event: WatchEvent) -> None:
        if event.target == "ideas_inbox":
            self._normalize_idea_watch_event(event.path)
            return

        if event.target in {"tasks_queue", "specs_queue", "config"}:
            return

        write_runtime_event(
            self.paths,
            event_type="watcher_event_ignored",
            data={"target": event.target, "path": event.path.as_posix()},
        )

    def _normalize_idea_watch_event(self, idea_path: Path) -> None:
        if not idea_path.is_file():
            return

        try:
            content = idea_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return
        title, summary = self._derive_idea_title_summary(content, fallback=idea_path.stem)
        spec_id = self._safe_spec_id_from_idea_path(idea_path)
        try:
            idea_reference = str(idea_path.relative_to(self.paths.root))
        except ValueError:
            idea_reference = idea_path.as_posix()
        spec_doc = SpecDocument(
            spec_id=spec_id,
            title=title,
            summary=summary,
            source_type="idea",
            source_id=spec_id,
            goals=(summary,),
            constraints=("generated from ideas/inbox watcher event",),
            acceptance=("planner processes this idea-derived spec",),
            references=(idea_reference,),
            created_at=self._now(),
            created_by="watcher",
        )

        try:
            QueueStore(self.paths).enqueue_spec(spec_doc)
        except (OSError, QueueStateError):
            return

        write_runtime_event(
            self.paths,
            event_type="idea_normalized_to_spec",
            data={"idea_path": idea_path.as_posix(), "spec_id": spec_id},
        )

    @staticmethod
    def _safe_spec_id_from_idea_path(path: Path) -> str:
        normalized = _IDEA_ID_SANITIZER.sub("-", path.stem).strip("-.")
        if not normalized:
            normalized = "idea"
        return f"idea-{normalized}"

    @staticmethod
    def _derive_idea_title_summary(content: str, *, fallback: str) -> tuple[str, str]:
        lines = [line.strip() for line in content.splitlines() if line.strip()]
        title = fallback
        for line in lines:
            if line.startswith("#"):
                candidate = line.lstrip("#").strip()
                if candidate:
                    title = candidate
                    break
        if title == fallback and lines:
            title = lines[0]

        summary = ""
        for line in lines:
            candidate = line.lstrip("#").strip()
            if candidate and candidate != title:
                summary = candidate
                break
        if not summary:
            summary = f"Idea captured from {fallback}"
        return title, summary

    def _refresh_runtime_queue_depths(self, *, process_running: bool | None = None) -> None:
        assert self.snapshot is not None
        update: dict[str, object] = {
            "queue_depth_execution": self._execution_queue_depth(),
            "queue_depth_planning": self._planning_queue_depth(),
            "updated_at": self._now(),
        }
        if process_running is not None:
            update["process_running"] = process_running
        self.snapshot = self.snapshot.model_copy(update=update)

    def _run_reconciliation_if_needed(self) -> tuple[ReconciliationSignal, ...]:
        assert self.snapshot is not None
        assert self.counters is not None

        signals = collect_reconciliation_signals(
            snapshot=self.snapshot,
            counters=self.counters,
            execution_status_marker=self._status_marker_for_reconciliation(self.paths.execution_status_file),
            planning_status_marker=self._status_marker_for_reconciliation(self.paths.planning_status_file),
        )
        if not signals:
            return signals

        self.snapshot = self._apply_reconciliation_signals(self.snapshot, self.counters, signals)
        self.counters = load_recovery_counters(self.paths)
        self._refresh_runtime_queue_depths()
        save_snapshot(self.paths, self.snapshot)
        write_runtime_event(
            self.paths,
            event_type="runtime_reconciled",
            data={
                "signal_count": len(signals),
                "primary_signal": signals[0].code,
                "recovery_stage": (
                    signals[0].recommended_stage.value if signals[0].recommended_stage is not None else None
                ),
            },
        )
        return signals

    def _status_marker_for_reconciliation(self, path: Path) -> str:
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            return _INVALID_RECONCILIATION_MARKER

        normalized = raw.strip()
        lines = normalized.splitlines()
        if len(lines) != 1 or not lines[0]:
            return _INVALID_RECONCILIATION_MARKER
        return lines[0]

    def _handle_mailbox_command(
        self,
        envelope: MailboxCommandEnvelope,
    ) -> None:  # pragma: no cover - thin integration path
        assert self.snapshot is not None
        command = envelope.command.value
        if command == "pause":
            self.snapshot = self.snapshot.model_copy(update={"paused": True, "updated_at": self._now()})
            save_snapshot(self.paths, self.snapshot)
            return
        if command == "resume":
            self.snapshot = self.snapshot.model_copy(update={"paused": False, "updated_at": self._now()})
            save_snapshot(self.paths, self.snapshot)
            return
        if command == "stop":
            self.snapshot = self.snapshot.model_copy(
                update={"stop_requested": True, "process_running": False, "updated_at": self._now()}
            )
            save_snapshot(self.paths, self.snapshot)
            return
        if command == "clear_stale_state":
            self._clear_stale_state(reason=self._mailbox_reason(envelope, default="operator requested stale-state clear"))
            return
        if command == "retry_active":
            self._retry_active(
                reason=self._mailbox_reason(envelope, default="operator requested retry"),
                scope=self._mailbox_retry_scope(envelope),
            )
            return
        if command == "reload_config":
            self._reload_config_from_mailbox()
            return
        if command == "add_task":
            self._enqueue_task_from_mailbox(envelope)
            return
        if command == "add_spec":
            self._enqueue_spec_from_mailbox(envelope)
            return
        if command == "add_idea":
            self._enqueue_idea_from_mailbox(envelope)
            return
        raise ControlRoutingError(f"Unsupported mailbox command: {command}")

    def _reload_config_from_mailbox(self) -> None:
        assert self.snapshot is not None
        previous_run_style = self.config.runtime.run_style if self.config is not None else self.snapshot.runtime_mode
        reloaded_config = load_runtime_config(self.config_path)
        compile_outcome = compile_and_persist_workspace_plan(
            self.paths,
            config=reloaded_config,
            requested_mode_id=self.mode_id,
            assets_root=self.assets_root,
        )
        active_plan = compile_outcome.active_plan
        if active_plan is None:
            errors = ", ".join(compile_outcome.diagnostics.errors) or "compile failed"
            self.snapshot = self.snapshot.model_copy(
                update={
                    "last_reload_outcome": ReloadOutcome.FAILED_RETAINED_PREVIOUS_PLAN,
                    "last_reload_error": errors,
                    "updated_at": self._now(),
                }
            )
            save_snapshot(self.paths, self.snapshot)
            write_runtime_event(
                self.paths,
                event_type="runtime_config_reload_failed",
                data={
                    "error": errors,
                    "retained_previous_plan": False,
                },
            )
            return

        if not compile_outcome.diagnostics.ok:
            errors = ", ".join(compile_outcome.diagnostics.errors) or "compile failed"
            self.snapshot = self.snapshot.model_copy(
                update={
                    "last_reload_outcome": ReloadOutcome.FAILED_RETAINED_PREVIOUS_PLAN,
                    "last_reload_error": errors,
                    "updated_at": self._now(),
                }
            )
            save_snapshot(self.paths, self.snapshot)
            write_runtime_event(
                self.paths,
                event_type="runtime_config_reload_failed",
                data={
                    "error": errors,
                    "retained_previous_plan": True,
                    "compiled_plan_id": self.snapshot.compiled_plan_id,
                },
            )
            return

        if previous_run_style is not reloaded_config.runtime.run_style:
            if reloaded_config.runtime.run_style is RuntimeMode.DAEMON:
                self._acquire_daemon_ownership_lock()
            else:
                self._release_daemon_ownership_lock(force=False)

        self.config = reloaded_config
        self._rebuild_watcher_session()
        self.compiled_plan = active_plan
        self.snapshot = self.snapshot.model_copy(
            update={
                "runtime_mode": reloaded_config.runtime.run_style,
                "active_mode_id": active_plan.mode_id,
                "execution_loop_id": active_plan.execution_loop_id,
                "planning_loop_id": active_plan.planning_loop_id,
                "compiled_plan_id": active_plan.compiled_plan_id,
                "compiled_plan_path": str((self.paths.state_dir / "compiled_plan.json").relative_to(self.paths.root)),
                "config_version": fingerprint_runtime_config(reloaded_config),
                "watcher_mode": self._watcher_mode_value(),
                "last_reload_outcome": ReloadOutcome.APPLIED,
                "last_reload_error": None,
                "updated_at": self._now(),
            }
        )
        save_snapshot(self.paths, self.snapshot)
        write_runtime_event(
            self.paths,
            event_type="runtime_config_reloaded",
            data={
                "mode_id": active_plan.mode_id,
                "compiled_plan_id": active_plan.compiled_plan_id,
            },
        )

    def _enqueue_task_from_mailbox(self, envelope: MailboxCommandEnvelope) -> None:
        assert self.snapshot is not None
        payload = MailboxAddTaskPayload.model_validate(envelope.payload)
        destination = QueueStore(self.paths).enqueue_task(payload.document)
        self._refresh_runtime_queue_depths()
        save_snapshot(self.paths, self.snapshot)
        write_runtime_event(
            self.paths,
            event_type="mailbox_add_task_applied",
            data={
                "task_id": payload.document.task_id,
                "path": str(destination.relative_to(self.paths.root)),
            },
        )

    def _enqueue_spec_from_mailbox(self, envelope: MailboxCommandEnvelope) -> None:
        assert self.snapshot is not None
        payload = MailboxAddSpecPayload.model_validate(envelope.payload)
        destination = QueueStore(self.paths).enqueue_spec(payload.document)
        self._refresh_runtime_queue_depths()
        save_snapshot(self.paths, self.snapshot)
        write_runtime_event(
            self.paths,
            event_type="mailbox_add_spec_applied",
            data={
                "spec_id": payload.document.spec_id,
                "path": str(destination.relative_to(self.paths.root)),
            },
        )

    def _enqueue_idea_from_mailbox(self, envelope: MailboxCommandEnvelope) -> None:
        assert self.snapshot is not None
        payload = MailboxAddIdeaPayload.model_validate(envelope.payload)
        destination_dir = self.paths.root / "ideas" / "inbox"
        destination_dir.mkdir(parents=True, exist_ok=True)
        destination = destination_dir / payload.source_name
        if destination.exists():
            raise WorkspaceStateError(f"idea document already exists: {destination}")
        destination.write_text(payload.markdown, encoding="utf-8")
        self._refresh_runtime_queue_depths()
        save_snapshot(self.paths, self.snapshot)
        write_runtime_event(
            self.paths,
            event_type="mailbox_add_idea_applied",
            data={
                "source_name": payload.source_name,
                "path": str(destination.relative_to(self.paths.root)),
            },
        )

    def _claim_next_work_item(self) -> None:
        queue = QueueStore(self.paths)
        claim = queue.claim_next_planning_item()
        if claim is not None:
            self._activate_claim(claim)
            return

        claim = queue.claim_next_execution_task()
        if claim is not None:
            self._activate_claim(claim)

    def _activate_claim(self, claim: QueueClaim) -> None:
        assert self.snapshot is not None
        stage = self._entry_stage_for_kind(claim.work_item_kind)
        active_plane = (
            Plane.PLANNING
            if claim.work_item_kind in {WorkItemKind.SPEC, WorkItemKind.INCIDENT}
            else Plane.EXECUTION
        )
        self.snapshot = self.snapshot.model_copy(
            update={
                "active_plane": active_plane,
                "active_stage": stage,
                "active_run_id": self._new_run_id(),
                "active_work_item_kind": claim.work_item_kind,
                "active_work_item_id": claim.work_item_id,
                "active_since": self._now(),
                "current_failure_class": None,
                "updated_at": self._now(),
            }
        )
        save_snapshot(self.paths, self.snapshot)

    def _apply_reconciliation_signals(
        self,
        snapshot: RuntimeSnapshot,
        counters: RecoveryCounters,
        signals: tuple[ReconciliationSignal, ...],
    ) -> RuntimeSnapshot:
        signal = signals[0]
        plane = signal.plane or Plane.EXECUTION
        stage = signal.recommended_stage
        if stage is None:
            return snapshot
        updated = snapshot.model_copy(
            update={
                "active_plane": plane,
                "active_stage": stage,
                "active_run_id": self._new_run_id(),
                "active_since": self._now(),
                "current_failure_class": signal.failure_class,
            }
        )
        return self._set_recovery_counters(updated, counters, signal.failure_class, stage)

    def _set_recovery_counters(
        self,
        snapshot: RuntimeSnapshot,
        counters: RecoveryCounters,
        failure_class: str,
        stage: StageName,
    ) -> RuntimeSnapshot:
        if snapshot.active_work_item_kind is None or snapshot.active_work_item_id is None:
            return snapshot
        if isinstance(stage, ExecutionStageName) and stage is ExecutionStageName.TROUBLESHOOTER:
            return self._increment_counter_field(
                snapshot,
                counters,
                failure_class=failure_class,
                work_item_kind=snapshot.active_work_item_kind,
                work_item_id=snapshot.active_work_item_id,
                field="troubleshoot_attempt_count",
            )
        if isinstance(stage, PlanningStageName) and stage is PlanningStageName.MECHANIC:
            return self._increment_counter_field(
                snapshot,
                counters,
                failure_class=failure_class,
                work_item_kind=snapshot.active_work_item_kind,
                work_item_id=snapshot.active_work_item_id,
                field="mechanic_attempt_count",
            )
        return snapshot

    def _route_stage_result(self, stage_result: StageResultEnvelope) -> RouterDecision:
        assert self.snapshot is not None
        assert self.counters is not None
        if stage_result.plane is Plane.EXECUTION:
            return next_execution_step(
                self.snapshot,
                stage_result,
                self.counters,
                max_fix_cycles=self.config.recovery.max_fix_cycles if self.config else 2,
                max_troubleshoot_attempts_before_consult=(
                    self.config.recovery.max_troubleshoot_attempts_before_consult if self.config else 2
                ),
            )
        return next_planning_step(
            self.snapshot,
            stage_result,
            self.counters,
            max_mechanic_attempts=self.config.recovery.max_mechanic_attempts if self.config else 2,
        )

    def _apply_router_decision(self, decision: RouterDecision, stage_result: StageResultEnvelope) -> None:
        assert self.snapshot is not None
        assert self.counters is not None

        if decision.action is RouterAction.RUN_STAGE:
            next_stage = decision.next_stage
            assert next_stage is not None
            updated = self.snapshot.model_copy(
                update={
                    "active_plane": Plane.EXECUTION if isinstance(next_stage, ExecutionStageName) else Plane.PLANNING,
                    "active_stage": next_stage,
                    "active_since": self._now(),
                    "current_failure_class": decision.failure_class,
                    "updated_at": self._now(),
                }
            )
            self.snapshot = self._increment_route_counters(updated, decision, stage_result)
            return

        if decision.action is RouterAction.IDLE:
            self._mark_active_work_item_complete(stage_result)
            self.snapshot = self.snapshot.model_copy(
                update={
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
                    "execution_status_marker": "### IDLE",
                    "planning_status_marker": "### IDLE",
                    "updated_at": self._now(),
                }
            )
            save_snapshot(self.paths, self.snapshot)
            set_execution_status(self.paths, "### IDLE")
            set_planning_status(self.paths, "### IDLE")
            reset_forward_progress_counters(
                self.paths,
                work_item_kind=stage_result.work_item_kind,
                work_item_id=stage_result.work_item_id,
            )
            self.counters = load_recovery_counters(self.paths)
            return

        if decision.action is RouterAction.HANDOFF:
            if decision.create_incident:
                self._enqueue_handoff_incident(decision=decision, stage_result=stage_result)
            self._mark_active_work_item_blocked_with_recovery(
                stage_result,
                reason="handoff",
            )
            self.snapshot = self.snapshot.model_copy(
                update={
                    "active_plane": None,
                    "active_stage": None,
                    "active_run_id": None,
                    "active_work_item_kind": None,
                    "active_work_item_id": None,
                    "active_since": None,
                    "current_failure_class": decision.failure_class,
                    "troubleshoot_attempt_count": 0,
                    "mechanic_attempt_count": 0,
                    "fix_cycle_count": 0,
                    "consultant_invocations": 0,
                    "updated_at": self._now(),
                }
            )
            save_snapshot(self.paths, self.snapshot)
            reset_forward_progress_counters(
                self.paths,
                work_item_kind=stage_result.work_item_kind,
                work_item_id=stage_result.work_item_id,
            )
            self.counters = load_recovery_counters(self.paths)
            return

        if decision.action is RouterAction.BLOCKED:
            self._mark_active_work_item_blocked_with_recovery(
                stage_result,
                reason="blocked",
            )
            self.snapshot = self.snapshot.model_copy(
                update={
                    "active_plane": None,
                    "active_stage": None,
                    "active_run_id": None,
                    "active_work_item_kind": None,
                    "active_work_item_id": None,
                    "active_since": None,
                    "current_failure_class": decision.failure_class,
                    "troubleshoot_attempt_count": 0,
                    "mechanic_attempt_count": 0,
                    "fix_cycle_count": 0,
                    "consultant_invocations": 0,
                    "updated_at": self._now(),
                }
            )
            save_snapshot(self.paths, self.snapshot)
            reset_forward_progress_counters(
                self.paths,
                work_item_kind=stage_result.work_item_kind,
                work_item_id=stage_result.work_item_id,
            )
            self.counters = load_recovery_counters(self.paths)

    def _increment_route_counters(
        self,
        snapshot: RuntimeSnapshot,
        decision: RouterDecision,
        stage_result: StageResultEnvelope,
    ) -> RuntimeSnapshot:
        assert self.counters is not None
        work_item_kind = snapshot.active_work_item_kind
        work_item_id = snapshot.active_work_item_id
        if work_item_kind is None or work_item_id is None:
            return snapshot
        if decision.next_stage is ExecutionStageName.TROUBLESHOOTER:
            snapshot = self._increment_counter_field(
                snapshot,
                self.counters,
                failure_class=decision.failure_class or "recoverable_failure",
                work_item_kind=work_item_kind,
                work_item_id=work_item_id,
                field="troubleshoot_attempt_count",
            )
        elif decision.next_stage is PlanningStageName.MECHANIC:
            snapshot = self._increment_counter_field(
                snapshot,
                self.counters,
                failure_class=decision.failure_class or "recoverable_failure",
                work_item_kind=work_item_kind,
                work_item_id=work_item_id,
                field="mechanic_attempt_count",
            )
        elif decision.next_stage is ExecutionStageName.CONSULTANT:
            snapshot = self._increment_counter_field(
                snapshot,
                self.counters,
                failure_class=decision.failure_class or "recoverable_failure",
                work_item_kind=work_item_kind,
                work_item_id=work_item_id,
                field="consultant_invocations",
            )
        elif stage_result.terminal_result is ExecutionTerminalResult.FIX_NEEDED:
            snapshot = self._increment_counter_field(
                snapshot,
                self.counters,
                failure_class=decision.failure_class or "fix_cycle",
                work_item_kind=work_item_kind,
                work_item_id=work_item_id,
                field="fix_cycle_count",
            )
        return snapshot

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
        timestamp = self._now()
        mutable_entries = list(counters.entries)
        for index, entry in enumerate(mutable_entries):
            if (
                entry.failure_class == failure_class
                and entry.work_item_kind is work_item_kind
                and entry.work_item_id == work_item_id
            ):
                mutable_entries[index] = entry.model_copy(
                    update={field: getattr(entry, field) + 1, "last_updated_at": timestamp}
                )
                break
        else:
            mutable_entries.append(
                RecoveryCounterEntry(
                    failure_class=failure_class,
                    work_item_kind=work_item_kind,
                    work_item_id=work_item_id,
                    troubleshoot_attempt_count=1 if field == "troubleshoot_attempt_count" else 0,
                    mechanic_attempt_count=1 if field == "mechanic_attempt_count" else 0,
                    fix_cycle_count=1 if field == "fix_cycle_count" else 0,
                    consultant_invocations=1 if field == "consultant_invocations" else 0,
                    last_updated_at=timestamp,
                )
            )
        updated_counters = RecoveryCounters(entries=tuple(mutable_entries))
        self.counters = updated_counters
        save_recovery_counters(self.paths, updated_counters)
        updated_snapshot = snapshot.model_copy(
            update={field: getattr(snapshot, field) + 1, "updated_at": self._now()}
        )
        self.snapshot = updated_snapshot
        return updated_snapshot

    def _mark_active_work_item_complete(self, stage_result: StageResultEnvelope) -> None:
        queue = QueueStore(self.paths)
        if stage_result.work_item_kind is WorkItemKind.TASK:
            queue.mark_task_done(stage_result.work_item_id)
            return
        if stage_result.work_item_kind is WorkItemKind.SPEC:
            queue.mark_spec_done(stage_result.work_item_id)
            return
        if stage_result.work_item_kind is WorkItemKind.INCIDENT:
            queue.mark_incident_resolved(stage_result.work_item_id)

    def _mark_active_work_item_blocked(self, stage_result: StageResultEnvelope) -> None:
        queue = QueueStore(self.paths)
        if stage_result.work_item_kind is WorkItemKind.TASK:
            queue.mark_task_blocked(stage_result.work_item_id)
            return
        if stage_result.work_item_kind is WorkItemKind.SPEC:
            queue.mark_spec_blocked(stage_result.work_item_id)
            return
        if stage_result.work_item_kind is WorkItemKind.INCIDENT:
            queue.mark_incident_blocked(stage_result.work_item_id)

    def _mark_active_work_item_blocked_with_recovery(
        self,
        stage_result: StageResultEnvelope,
        *,
        reason: str,
    ) -> None:
        try:
            self._mark_active_work_item_blocked(stage_result)
        except QueueStateError as exc:
            write_runtime_event(
                self.paths,
                event_type="runtime_blocked_mark_failed",
                data={
                    "reason": reason,
                    "work_item_kind": stage_result.work_item_kind.value,
                    "work_item_id": stage_result.work_item_id,
                    "error": str(exc),
                },
            )

    def _enqueue_handoff_incident(
        self,
        *,
        decision: RouterDecision,
        stage_result: StageResultEnvelope,
    ) -> Path:
        queue = QueueStore(self.paths)
        incident_id = f"incident-{stage_result.work_item_id}-{uuid4().hex[:8]}"
        source_task_id = (
            stage_result.work_item_id if stage_result.work_item_kind is WorkItemKind.TASK else None
        )
        source_spec_id = (
            stage_result.work_item_id if stage_result.work_item_kind is WorkItemKind.SPEC else None
        )
        incident = IncidentDocument(
            incident_id=incident_id,
            title=f"Planning handoff for {stage_result.work_item_kind.value} {stage_result.work_item_id}",
            summary=(
                f"Stage {stage_result.stage.value} returned {stage_result.terminal_result.value}; "
                "planning remediation required."
            ),
            source_task_id=source_task_id,
            source_spec_id=source_spec_id,
            source_stage=stage_result.stage,
            source_plane=stage_result.plane,
            failure_class=decision.failure_class or "consultant_needs_planning",
            severity=IncidentSeverity.HIGH,
            needs_planning=True,
            trigger_reason=decision.reason,
            observed_symptoms=stage_result.notes,
            failed_attempts=(),
            consultant_decision=IncidentDecision.NEEDS_PLANNING,
            evidence_paths=stage_result.artifact_paths,
            related_run_ids=(stage_result.run_id,),
            related_stage_results=(
                self.snapshot.last_stage_result_path,
            )
            if self.snapshot is not None and self.snapshot.last_stage_result_path is not None
            else (),
            references=(),
            opened_at=self._now(),
            opened_by="runtime",
        )
        destination = queue.enqueue_incident(incident)
        write_runtime_event(
            self.paths,
            event_type="runtime_handoff_incident_enqueued",
            data={
                "incident_id": incident_id,
                "source_work_item_kind": stage_result.work_item_kind.value,
                "source_work_item_id": stage_result.work_item_id,
                "destination": str(destination.relative_to(self.paths.root)),
            },
        )
        return destination

    def _write_stage_result(
        self,
        request: StageRunRequest,
        stage_result: StageResultEnvelope,
    ) -> Path:
        run_dir = Path(request.run_dir)
        stage_result_dir = run_dir / "stage_results"
        stage_result_dir.mkdir(parents=True, exist_ok=True)
        stage_result_path = stage_result_dir / f"{request.request_id}.json"
        stage_result_path.write_text(stage_result.model_dump_json(indent=2) + "\n", encoding="utf-8")
        return stage_result_path

    def _write_plane_status(self, stage_result: StageResultEnvelope) -> None:
        assert self.snapshot is not None
        if stage_result.plane is Plane.EXECUTION:
            set_execution_status(self.paths, stage_result.summary_status_marker)
            self.snapshot = self.snapshot.model_copy(
                update={"execution_status_marker": stage_result.summary_status_marker}
            )
            return
        set_planning_status(self.paths, stage_result.summary_status_marker)
        self.snapshot = self.snapshot.model_copy(
            update={"planning_status_marker": stage_result.summary_status_marker}
        )

    def _build_stage_run_request(self, stage_plan: FrozenStagePlan) -> StageRunRequest:
        assert self.snapshot is not None
        active_path = self._active_work_item_path(
            self.snapshot.active_work_item_kind,
            self.snapshot.active_work_item_id,
        )
        run_id = self.snapshot.active_run_id or self._new_run_id()
        run_dir = self.paths.runs_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        request = StageRunRequest(
            request_id=self._new_request_id(),
            run_id=run_id,
            plane=stage_plan.plane,
            stage=stage_plan.stage,
            mode_id=self.snapshot.active_mode_id,
            compiled_plan_id=self.snapshot.compiled_plan_id,
            entrypoint_path=str(self.paths.runtime_root / stage_plan.entrypoint_path),
            entrypoint_contract_id=stage_plan.entrypoint_contract_id,
            required_skill_paths=tuple(
                str(self.paths.runtime_root / path) for path in stage_plan.required_skills
            ),
            attached_skill_paths=tuple(
                str(self.paths.runtime_root / path) for path in stage_plan.attached_skill_additions
            ),
            active_work_item_kind=self.snapshot.active_work_item_kind,
            active_work_item_id=self.snapshot.active_work_item_id,
            active_work_item_path=str(active_path) if active_path is not None else None,
            run_dir=str(run_dir),
            summary_status_path=str(
                self.paths.execution_status_file
                if stage_plan.plane is Plane.EXECUTION
                else self.paths.planning_status_file
            ),
            runtime_snapshot_path=str(self.paths.runtime_snapshot_file),
            recovery_counters_path=str(self.paths.recovery_counters_file),
            preferred_troubleshoot_report_path=str(run_dir / "troubleshoot_report.md"),
            runner_name=stage_plan.runner_name,
            model_name=stage_plan.model_name,
            timeout_seconds=stage_plan.timeout_seconds,
        )
        self.snapshot = self.snapshot.model_copy(update={"active_run_id": request.run_id})
        save_snapshot(self.paths, self.snapshot)
        return request

    def _stage_plan_for(self, plane: Plane, stage: StageName) -> FrozenStagePlan:
        assert self.compiled_plan is not None
        for stage_plan in self.compiled_plan.stage_plans:
            if stage_plan.plane is plane and stage_plan.stage is stage:
                return stage_plan
        raise KeyError(f"No compiled stage plan for {plane.value}:{stage.value}")

    def _entry_stage_for_kind(self, work_item_kind: WorkItemKind) -> StageName:
        if work_item_kind is WorkItemKind.TASK:
            return ExecutionStageName.BUILDER
        if work_item_kind is WorkItemKind.SPEC:
            return PlanningStageName.PLANNER
        return PlanningStageName.AUDITOR

    def _idle_stage_for_no_work(self) -> StageName:
        return ExecutionStageName.UPDATER

    def _idle_tick_outcome(self, *, reason: str) -> RuntimeTickOutcome:
        assert self.snapshot is not None
        idle_stage = self._idle_stage_for_no_work()
        stage_result = StageResultEnvelope(
            run_id="idle",
            plane=Plane.EXECUTION,
            stage=idle_stage,
            work_item_kind=WorkItemKind.TASK,
            work_item_id="idle",
            terminal_result=ExecutionTerminalResult.UPDATE_COMPLETE,
            result_class=ResultClass.SUCCESS,
            summary_status_marker=f"### {ExecutionTerminalResult.UPDATE_COMPLETE.value}",
            success=True,
            retryable=False,
            exit_code=0,
            duration_seconds=0,
            started_at=self._now(),
            completed_at=self._now(),
        )
        return RuntimeTickOutcome(
            stage=idle_stage,
            stage_result=stage_result,
            stage_result_path=self.paths.logs_dir / "idle-stage-result.json",
            router_decision=RouterDecision(
                action=RouterAction.IDLE,
                next_plane=None,
                next_stage=None,
                reason=reason,
            ),
            snapshot=self.snapshot,
        )

    def _active_work_item_path(
        self,
        work_item_kind: WorkItemKind | None,
        work_item_id: str | None,
    ) -> Path | None:
        if work_item_kind is None or work_item_id is None:
            return None
        if work_item_kind is WorkItemKind.TASK:
            return self.paths.tasks_active_dir / f"{work_item_id}.md"
        if work_item_kind is WorkItemKind.SPEC:
            return self.paths.specs_active_dir / f"{work_item_id}.md"
        return self.paths.incidents_active_dir / f"{work_item_id}.md"

    def _clear_stale_state(self, *, reason: str = "runtime stale-state clear") -> None:
        queue = QueueStore(self.paths)
        self._requeue_all_active_items(queue, reason=reason)
        self._reset_runtime_to_idle(clear_stop_requested=True, clear_paused=True)
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

        self._reset_runtime_to_idle(clear_stop_requested=False, clear_paused=False)
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

    def _reset_runtime_to_idle(self, *, clear_stop_requested: bool, clear_paused: bool) -> None:
        assert self.snapshot is not None
        update: dict[str, object] = {
            "process_running": True,
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
        self.snapshot = self.snapshot.model_copy(update=update)
        save_snapshot(self.paths, self.snapshot)
        set_execution_status(self.paths, _STATUS_IDLE)
        set_planning_status(self.paths, _STATUS_IDLE)

    @staticmethod
    def _mailbox_reason(envelope: MailboxCommandEnvelope, *, default: str) -> str:
        value = envelope.payload.get("reason")
        if isinstance(value, str) and value.strip():
            return value.strip()
        return default

    @staticmethod
    def _mailbox_retry_scope(envelope: MailboxCommandEnvelope) -> Plane | None:
        value = envelope.payload.get("scope")
        if value is None:
            return None
        if not isinstance(value, str):
            raise ControlRoutingError("retry_active scope must be a string")
        try:
            return Plane(value)
        except ValueError as exc:
            raise ControlRoutingError(f"Unsupported retry_active scope: {value}") from exc

    def _execution_queue_depth(self) -> int:
        return len(list(self.paths.tasks_queue_dir.glob("*.md")))

    def _planning_queue_depth(self) -> int:
        spec_depth = len(list(self.paths.specs_queue_dir.glob("*.md")))
        incident_depth = len(list(self.paths.incidents_incoming_dir.glob("*.md")))
        return spec_depth + incident_depth

    def _runner_failure_result(
        self,
        request: StageRunRequest,
        *,
        failure_class: str,
        error: str,
    ) -> RunnerRawResult:
        now = self._now()
        return RunnerRawResult(
            request_id=request.request_id,
            run_id=request.run_id,
            stage=request.stage,
            runner_name=request.runner_name or "runtime",
            model_name=request.model_name,
            exit_kind="runner_error",
            exit_code=1,
            stdout_path=None,
            stderr_path=None,
            terminal_result_path=None,
            started_at=now,
            ended_at=now,
        )

    def _requires_daemon_ownership_lock(self) -> bool:
        return self.config is not None and self.config.runtime.run_style is RuntimeMode.DAEMON

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
        return f"run-{uuid4().hex}"

    def _new_request_id(self) -> str:
        return f"request-{uuid4().hex}"

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)


__all__ = ["RuntimeEngine", "RuntimeTickOutcome"]
