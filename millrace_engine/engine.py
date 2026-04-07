"""Top-level runtime supervisor."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Literal

from .adapters.file_watcher import FileWatcherAdapter, RuntimeInputEvent, RuntimeInputKind
from .config import (
    ConfigApplyBoundary,
    LoadedConfig,
    build_runtime_paths,
    diff_config_fields,
    load_engine_config,
)
from .contracts import CompletionDecision, ExecutionStatus, ResearchMode
from .control_common import ControlError
from .control_models import OperationResult, RuntimeState
from .control_mutations import _assert_reload_safe
from .control_reports import (
    build_live_runtime_state,
    config_hash,
    decision_report_paths,
    write_runtime_state,
)
from .events import EventBus, EventSource, EventType, HistorySubscriber, JsonlEventSubscriber
from .engine_mailbox_processor import EngineMailboxHooks, EngineMailboxProcessor
from .planes.base import StageCommandMap
from .planes.execution import ExecutionCycleResult, ExecutionPlane
from .planes.research import ResearchPlane
from .research.audit import AuditExecutionError
from .research.incidents import IncidentExecutionError
from .research.dispatcher import ResearchDispatchError
from .policies import (
    DefaultOutageProbe,
    OutageAction,
    OutageAttempt,
    OutagePolicyError,
    OutagePolicySnapshot,
    OutageProbe,
    OutageRoute,
    OutageTrigger,
    TransportProbe,
    append_outage_attempt_log,
    evaluate_outage_attempt,
    outage_policy_record,
)
from .provenance import BoundExecutionParameters, TransitionHistoryStore
from .queue import TaskQueue, load_research_recovery_latch
from .research.governance import sync_progress_watchdog


class MillraceEngine:
    """Async-owned runtime supervisor."""

    def __init__(
        self,
        config_path: Path | str = "millrace.toml",
        *,
        stage_commands: StageCommandMap | None = None,
        transport_probe: TransportProbe | None = None,
        outage_probe: OutageProbe | None = None,
    ) -> None:
        self.config_path = Path(config_path).expanduser().resolve()
        self.stage_commands = stage_commands
        self._transport_probe = transport_probe
        self._outage_probe = outage_probe or DefaultOutageProbe()
        self._config_lock = RLock()
        self.loaded = load_engine_config(self.config_path)
        self.paths = build_runtime_paths(self.loaded.config)
        self.pending_loaded: LoadedConfig | None = None
        self.previous_loaded: LoadedConfig | None = None
        self.pending_boundary: ConfigApplyBoundary | None = None
        self.pending_changed_fields: tuple[str, ...] = ()
        self.rollback_armed = False
        self.execution_plane = ExecutionPlane(
            self.loaded.config,
            self.paths,
            stage_commands=self.stage_commands,
            before_stage=self._before_stage_boundary,
            emit_event=lambda event_type, payload: self.event_bus.emit(
                event_type,
                source=EventSource.EXECUTION,
                payload=payload,
            ),
            transport_probe=self._transport_probe,
        )
        self.research_plane = ResearchPlane(self.loaded.config, self.paths)
        self.event_bus = EventBus(
            [
                JsonlEventSubscriber(self.paths),
                HistorySubscriber(self.paths),
                self.research_plane,
            ]
        )
        self.research_plane.bind_emitter(
            lambda event_type, payload: self.event_bus.emit(
                event_type,
                source=EventSource.RESEARCH,
                payload=payload,
            )
        )
        self.mailbox_processor = EngineMailboxProcessor(
            config_path=self.config_path,
            hooks=EngineMailboxHooks(
                get_paths=lambda: self.paths,
                get_loaded=lambda: self.loaded,
                emit_event=lambda event_type, source, payload: self.event_bus.emit(
                    event_type,
                    source=source,
                    payload=payload,
                ),
                queue_or_apply_reloaded_config=self._queue_or_apply_reloaded_config,
                restart_file_watcher=self._restart_file_watcher,
                consume_research_recovery_latch=self._consume_research_recovery_latch,
                get_stop_requested=lambda: self.stop_requested,
                set_stop_requested=self._set_stop_requested,
                get_paused=lambda: self.paused,
                set_pause_state=self._set_pause_state,
            ),
        )
        self.started_at: datetime | None = None
        self.paused = False
        self.pause_reason: str | None = None
        self.pause_run_id: str | None = None
        self.stop_requested = False
        self.input_queue: asyncio.Queue[RuntimeInputEvent] | None = None
        self.file_watcher: FileWatcherAdapter | None = None

    @property
    def state_path(self) -> Path:
        return self.paths.runtime_dir / "state.json"

    def _set_stop_requested(self, requested: bool) -> None:
        self.stop_requested = requested

    def _set_pause_state(self, paused: bool, reason: str | None, run_id: str | None) -> None:
        self.paused = paused
        self.pause_reason = reason
        self.pause_run_id = run_id

    def _install_loaded_config(self, loaded: LoadedConfig) -> None:
        self.loaded = loaded
        self.paths = build_runtime_paths(self.loaded.config)
        self.execution_plane.reconfigure(self.loaded.config, self.paths)
        self.research_plane.reconfigure(self.loaded.config, self.paths)

    def _sync_ready_research_dispatch(self, *, trigger: str):
        if self.loaded.config.research.mode is ResearchMode.STUB:
            return None
        if self.research_plane.snapshot_state().checkpoint is None:
            thawed = self._consume_research_recovery_latch(trigger=f"research_sync:{trigger}")
            if thawed > 0:
                self._request_stop_if_completion_honored()
                return None
        try:
            dispatch = self.research_plane.sync_runtime(trigger=trigger)
        except (ResearchDispatchError, IncidentExecutionError, AuditExecutionError):
            return None
        self._request_stop_if_completion_honored()
        self._consume_research_recovery_latch(trigger=f"research_sync:{trigger}")
        return dispatch

    def _snapshot(self, *, process_running: bool, mode: Literal["once", "daemon"]) -> RuntimeState:
        with self._config_lock:
            loaded = self.loaded
            pending_loaded = self.pending_loaded
            previous_loaded = self.previous_loaded
            pending_boundary = self.pending_boundary
            pending_fields = self.pending_changed_fields
            rollback_armed = self.rollback_armed
        return build_live_runtime_state(
            loaded,
            process_running=process_running,
            paused=self.paused,
            pause_reason=self.pause_reason,
            pause_run_id=self.pause_run_id,
            started_at=self.started_at,
            mode=mode,
            pending_loaded=pending_loaded,
            previous_loaded=previous_loaded,
            pending_boundary=pending_boundary,
            pending_fields=pending_fields,
            rollback_armed=rollback_armed,
        )

    def _write_state(self, *, process_running: bool, mode: Literal["once", "daemon"]) -> RuntimeState:
        state = self._snapshot(process_running=process_running, mode=mode)
        write_runtime_state(self.state_path, state)
        return state

    def _watcher_restart_required(self, changed_fields: tuple[str, ...]) -> bool:
        return any(
            field == "engine.idle_mode"
            or field.startswith("engine.idle_mode.")
            or field == "watchers"
            or field.startswith("watchers.")
            for field in changed_fields
        )

    def _build_file_watcher(self) -> FileWatcherAdapter:
        if self.input_queue is None:
            raise RuntimeError("input queue must exist before building the file watcher")
        return FileWatcherAdapter(
            self.paths,
            emit=self._enqueue_input_event,
            config_path=self.config_path,
            watch_roots=self.loaded.config.watchers.roots,
            mode=self.loaded.config.engine.idle_mode,
            debounce_seconds=self.loaded.config.watchers.debounce_seconds,
            loop=asyncio.get_running_loop(),
        )

    def _boundary_allows(self, current: ConfigApplyBoundary, pending: ConfigApplyBoundary) -> bool:
        order = {
            ConfigApplyBoundary.LIVE_IMMEDIATE: 0,
            ConfigApplyBoundary.STAGE_BOUNDARY: 1,
            ConfigApplyBoundary.CYCLE_BOUNDARY: 2,
            ConfigApplyBoundary.STARTUP_ONLY: 3,
        }
        return order[pending] <= order[current]

    def _apply_loaded_config_locked(
        self,
        loaded: LoadedConfig,
        *,
        changed_fields: tuple[str, ...],
    ) -> tuple[str, str]:
        previous_hash = config_hash(self.loaded.config)
        self.previous_loaded = self.loaded
        self.pending_loaded = None
        self.pending_boundary = None
        self.pending_changed_fields = ()
        self.rollback_armed = True
        self._install_loaded_config(loaded)
        return previous_hash, config_hash(self.loaded.config)

    def _emit_config_changed(
        self,
        *,
        command_id: str | None,
        boundary: ConfigApplyBoundary,
        changed_fields: tuple[str, ...],
        loaded: LoadedConfig,
        key: str | None = None,
    ) -> None:
        payload = {
            "command_id": command_id,
            "boundary": boundary.value,
            "changed_fields": changed_fields,
            "pending_config_hash": config_hash(loaded.config),
        }
        if key is not None:
            payload["key"] = key
        self.event_bus.emit(
            EventType.CONFIG_CHANGED,
            source=EventSource.CONTROL,
            payload=payload,
        )

    def _emit_config_applied(
        self,
        *,
        command_id: str | None,
        boundary: ConfigApplyBoundary,
        changed_fields: tuple[str, ...],
        active_hash: str,
        previous_hash: str | None,
        rollback: bool = False,
        reason: str | None = None,
    ) -> None:
        payload = {
            "command_id": command_id,
            "boundary": boundary.value,
            "changed_fields": changed_fields,
            "config_hash": active_hash,
            "rollback": rollback,
        }
        if previous_hash is not None:
            payload["previous_config_hash"] = previous_hash
        if reason is not None:
            payload["reason"] = reason
        self.event_bus.emit(
            EventType.CONFIG_APPLIED,
            source=EventSource.CONTROL,
            payload=payload,
        )

    def _queue_or_apply_reloaded_config(
        self,
        loaded: LoadedConfig,
        *,
        command_id: str | None,
        key: str | None = None,
    ) -> tuple[OperationResult, bool]:
        applied_operation: OperationResult | None = None
        restart_watcher = False
        with self._config_lock:
            changed_fields = diff_config_fields(self.loaded.config, loaded.config)
            boundary = self.loaded.config.boundaries.classify_fields(changed_fields)
            if not changed_fields or boundary is None:
                return (
                    OperationResult(mode="direct", applied=False, message="config already current"),
                    False,
                )
            if boundary is ConfigApplyBoundary.STARTUP_ONLY:
                raise ControlError(f"cannot change startup-only field at runtime: {changed_fields[0]}")

            if boundary is ConfigApplyBoundary.LIVE_IMMEDIATE:
                self._emit_config_changed(
                    command_id=command_id,
                    boundary=boundary,
                    changed_fields=changed_fields,
                    loaded=loaded,
                    key=key,
                )
                previous_hash, active_hash = self._apply_loaded_config_locked(
                    loaded,
                    changed_fields=changed_fields,
                )
                self._emit_config_applied(
                    command_id=command_id,
                    boundary=boundary,
                    changed_fields=changed_fields,
                    active_hash=active_hash,
                    previous_hash=previous_hash,
                )
                applied_operation = OperationResult(
                    mode="direct",
                    applied=True,
                    message="config applied immediately",
                    payload={
                        "boundary": boundary.value,
                        "config_hash": active_hash,
                        "previous_config_hash": previous_hash,
                        "changed_fields": changed_fields,
                        **({"key": key} if key is not None else {}),
                    },
                )
                restart_watcher = self._watcher_restart_required(changed_fields)
            else:
                self.pending_loaded = loaded
                self.pending_boundary = boundary
                self.pending_changed_fields = changed_fields
                self._emit_config_changed(
                    command_id=command_id,
                    boundary=boundary,
                    changed_fields=changed_fields,
                    loaded=loaded,
                    key=key,
                )
                return (
                    OperationResult(
                        mode="direct",
                        applied=True,
                        message=f"config queued for {boundary.value}",
                        payload={
                            "boundary": boundary.value,
                            "pending_config_hash": config_hash(loaded.config),
                            "changed_fields": changed_fields,
                            **({"key": key} if key is not None else {}),
                        },
                    ),
                    False,
                )

        self._sync_ready_research_dispatch(trigger="config-applied")
        if applied_operation is None:
            raise RuntimeError("live config apply did not produce an operation result")
        return applied_operation, restart_watcher

    def _apply_pending_config_if_due(
        self,
        boundary: ConfigApplyBoundary,
        *,
        command_id: str | None = None,
    ) -> bool:
        with self._config_lock:
            if self.pending_loaded is None or self.pending_boundary is None:
                return False
            if not self._boundary_allows(boundary, self.pending_boundary):
                return False
            changed_fields = self.pending_changed_fields
            applied_boundary = self.pending_boundary
            previous_hash, active_hash = self._apply_loaded_config_locked(
                self.pending_loaded,
                changed_fields=changed_fields,
            )
        self._emit_config_applied(
            command_id=command_id,
            boundary=applied_boundary,
            changed_fields=changed_fields,
            active_hash=active_hash,
            previous_hash=previous_hash,
        )
        self._sync_ready_research_dispatch(trigger="config-applied")
        return self._watcher_restart_required(changed_fields)

    def _clear_rollback_guard(self) -> None:
        with self._config_lock:
            self.previous_loaded = None
            self.rollback_armed = False

    def _rollback_active_config(self, reason: str) -> bool:
        with self._config_lock:
            if not self.rollback_armed or self.previous_loaded is None:
                return False
            failed_hash = config_hash(self.loaded.config)
            changed_fields = diff_config_fields(self.previous_loaded.config, self.loaded.config)
            boundary = self.previous_loaded.config.boundaries.classify_fields(changed_fields) or ConfigApplyBoundary.STAGE_BOUNDARY
            rollback_target = self.previous_loaded
            self._install_loaded_config(rollback_target)
            restored_hash = config_hash(self.loaded.config)
            self.previous_loaded = None
            self.pending_loaded = None
            self.pending_boundary = None
            self.pending_changed_fields = ()
            self.rollback_armed = False
        self._emit_config_applied(
            command_id=None,
            boundary=boundary,
            changed_fields=changed_fields,
            active_hash=restored_hash,
            previous_hash=failed_hash,
            rollback=True,
            reason=reason,
        )
        return True

    def _emit_cycle_events(self, result: ExecutionCycleResult) -> None:
        if result.archived_task is not None:
            self.event_bus.emit(
                EventType.TASK_ARCHIVED,
                source=EventSource.EXECUTION,
                payload={"task_id": result.archived_task.task_id, "title": result.archived_task.title},
            )
        if result.quarantined_task is not None:
            self.event_bus.emit(
                EventType.TASK_QUARANTINED,
                source=EventSource.EXECUTION,
                payload={
                    "task_id": result.quarantined_task.task_id,
                    "title": result.quarantined_task.title,
                    "diagnostics_dir": result.diagnostics_dir,
                    "handoff_id": (
                        None
                        if result.research_handoff is None
                        else result.research_handoff.handoff_id
                    ),
                },
            )
            payload: dict[str, object] = {
                "task_id": result.quarantined_task.task_id,
                "title": result.quarantined_task.title,
                "run_id": result.run_id,
            }
            if result.research_handoff is not None:
                payload["handoff"] = result.research_handoff.model_dump(mode="json")
            self.event_bus.emit(
                EventType.NEEDS_RESEARCH,
                source=EventSource.EXECUTION,
                payload=payload,
            )
        if result.update_only:
            self.event_bus.emit(
                EventType.BACKLOG_EMPTY_AUDIT,
                source=EventSource.EXECUTION,
                payload={"backlog_depth": 0},
            )

    def _enqueue_input_event(self, event: RuntimeInputEvent) -> None:
        if self.input_queue is None:
            return
        self.input_queue.put_nowait(event)

    def _request_stop_if_completion_honored(self) -> bool:
        marker_path = self.paths.agents_dir / "AUTONOMY_COMPLETE"
        if not marker_path.exists() or not marker_path.is_file():
            return False
        _, completion_decision_path = decision_report_paths(self.paths)
        if not completion_decision_path.exists():
            return False
        try:
            decision = CompletionDecision.model_validate_json(
                completion_decision_path.read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            return False
        if decision.decision != "PASS":
            return False
        self.stop_requested = True
        return True

    def _emit_config_rejected(self, *, reason: str, path: Path | None = None) -> None:
        payload: dict[str, object] = {
            "rejected": True,
            "reason": reason,
        }
        if path is not None:
            payload["path"] = path
        self.event_bus.emit(
            EventType.CONFIG_CHANGED,
            source=EventSource.ADAPTER,
            payload=payload,
        )

    async def _reload_config_from_disk(self, *, trigger_path: Path | None = None) -> bool:
        reloaded: LoadedConfig | None = None
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                reloaded = load_engine_config(self.config_path)
                _assert_reload_safe(self.loaded, reloaded)
            except Exception as exc:  # noqa: BLE001 - config reload must not crash the daemon
                last_error = exc
                if attempt == 2:
                    self._emit_config_rejected(reason=str(exc), path=trigger_path)
                    return False
                await asyncio.sleep(0.1 * (attempt + 1))
                continue
            break

        if reloaded is None:
            if last_error is not None:
                self._emit_config_rejected(reason=str(last_error), path=trigger_path)
            return False

        operation, restart_watcher = self._queue_or_apply_reloaded_config(reloaded, command_id=None)
        if restart_watcher:
            await self._restart_file_watcher()
        return operation.applied

    def _consume_research_recovery_latch(
        self,
        *,
        trigger: str,
        command_id: str | None = None,
        path: Path | None = None,
    ) -> int:
        sync_progress_watchdog(paths=self.paths, allow_regeneration=True)
        latch_state = load_research_recovery_latch(self.paths.research_recovery_latch_file)
        if latch_state is None:
            return 0

        queue = TaskQueue(self.paths)
        thawed = queue.thaw(latch_state)
        if thawed <= 0:
            sync_progress_watchdog(paths=self.paths, allow_regeneration=False)
            return 0

        sync_progress_watchdog(paths=self.paths, allow_regeneration=False)

        next_task = queue.peek_next()
        decision = latch_state.remediation_decision
        self.event_bus.emit(
            EventType.BACKLOG_REPOPULATED,
            source=EventSource.ENGINE,
            payload={
                "trigger": trigger,
                "command_id": command_id,
                "path": path,
                "batch_id": latch_state.batch_id,
                "failure_signature": latch_state.failure_signature,
                "thawed_cards": thawed,
                "backlog_depth": queue.backlog_depth(),
                "next_task_id": next_task.task_id if next_task is not None else None,
                "next_task_title": next_task.title if next_task is not None else None,
                "handoff_id": (
                    None if latch_state.handoff is None else latch_state.handoff.handoff_id
                ),
                "parent_run_id": (
                    None
                    if latch_state.handoff is None or latch_state.handoff.parent_run is None
                    else latch_state.handoff.parent_run.run_id
                ),
                "decision_type": (
                    None if decision is None else decision.decision_type
                ),
                "remediation_spec_id": (
                    None if decision is None else decision.remediation_spec_id
                ),
                "remediation_record_path": (
                    None if decision is None else decision.remediation_record_path
                ),
                "taskaudit_record_path": (
                    None if decision is None else decision.taskaudit_record_path
                ),
                "task_provenance_path": (
                    None if decision is None else decision.task_provenance_path
                ),
                "lineage_path": (
                    None if decision is None else decision.lineage_path
                ),
            },
        )
        sync_progress_watchdog(paths=self.paths, allow_regeneration=False)
        return thawed

    async def _handle_input_event(self, event: RuntimeInputEvent) -> None:
        if event.kind is RuntimeInputKind.BACKLOG_CHANGED:
            self._consume_research_recovery_latch(trigger="backlog_changed", path=event.path)
            return
        if event.kind is RuntimeInputKind.CONFIG_CHANGED:
            await self._reload_config_from_disk(trigger_path=event.path)
            return
        if event.kind is RuntimeInputKind.CONTROL_COMMAND_AVAILABLE:
            await self.mailbox_processor.process_mailbox()
            return
        if event.kind is RuntimeInputKind.IDEA_SUBMITTED:
            self.event_bus.emit(
                EventType.IDEA_SUBMITTED,
                source=EventSource.ADAPTER,
                payload={"path": event.path},
            )
            return
        if event.kind is RuntimeInputKind.STOP_AUTONOMY:
            self.stop_requested = True
            return
        if event.kind is RuntimeInputKind.AUTONOMY_COMPLETE:
            self._request_stop_if_completion_honored()
            return

    async def _drain_input_queue(self) -> None:
        if self.input_queue is None:
            return
        while True:
            try:
                event = self.input_queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            try:
                await self._handle_input_event(event)
            finally:
                self.input_queue.task_done()

    async def _restart_file_watcher(self) -> None:
        if self.file_watcher is not None:
            self.file_watcher.stop()
        if self.input_queue is None:
            return
        self.file_watcher = self._build_file_watcher()
        if self.file_watcher.mode == "watch":
            self.file_watcher.start()

    async def _ingest_poll_fallback_events(self) -> None:
        if self.file_watcher is None or self.file_watcher.mode != "poll":
            return
        for event in self.file_watcher.poll_once():
            await self._handle_input_event(event)

    async def _wait_for_wakeup(self) -> None:
        timeout = self.loaded.config.engine.poll_interval_seconds
        if self.file_watcher is None or self.file_watcher.mode != "watch" or self.input_queue is None:
            effective_timeout = timeout
            if self.file_watcher is not None:
                effective_timeout = self.file_watcher.wakeup_timeout_seconds(timeout)
            await asyncio.sleep(effective_timeout)
            return
        try:
            event = await asyncio.wait_for(self.input_queue.get(), timeout=timeout)
        except TimeoutError:
            return
        try:
            await self._handle_input_event(event)
        finally:
            self.input_queue.task_done()
        await self._drain_input_queue()

    def _before_stage_boundary(self, stage: object) -> None:
        del stage
        self.mailbox_processor.process_config_mailbox_between_stages()
        self._apply_pending_config_if_due(ConfigApplyBoundary.STAGE_BOUNDARY)

    def _outage_bound_parameters(self, trigger: OutageTrigger) -> BoundExecutionParameters:
        stage = trigger.evaluation.facts.stage
        if stage is None:
            return BoundExecutionParameters()
        return BoundExecutionParameters(
            model_profile_ref=stage.model_profile_ref,
            runner=stage.runner,
            model=stage.model,
            effort=stage.effort,
            allow_search=stage.allow_search,
            timeout_seconds=stage.timeout_seconds,
        )

    def _append_outage_policy_record(
        self,
        history: TransitionHistoryStore,
        *,
        trigger: OutageTrigger,
        record,
        status_before: ExecutionStatus,
        status_after: ExecutionStatus,
        active_task_after: str | None,
    ) -> None:
        stage = trigger.evaluation.facts.stage
        history.append(
            event_name="policy.outage.recovery",
            source=record.evaluator,
            plane=record.facts.plane,
            node_id=trigger.node_id,
            kind_id=stage.kind_id if stage is not None else "execution.policy_hook",
            outcome=record.decision.value,
            status_before=status_before.value,
            status_after=status_after.value,
            active_task_before=trigger.task_id,
            active_task_after=active_task_after,
            bound_execution_parameters=self._outage_bound_parameters(trigger),
            policy_evaluation=record,
            attributes={"policy_hook": record.hook.value, "routing_mode": "outage_recovery"},
        )

    async def _sleep_with_mailbox_activity(self, delay_seconds: int) -> None:
        if delay_seconds <= 0:
            await self._drain_input_queue()
            await self._ingest_poll_fallback_events()
            await self.mailbox_processor.process_mailbox()
            return
        loop = asyncio.get_running_loop()
        deadline = loop.time() + delay_seconds
        while not self.stop_requested and not self.paused:
            await self._drain_input_queue()
            await self._ingest_poll_fallback_events()
            await self.mailbox_processor.process_mailbox()
            if self.stop_requested or self.paused:
                return
            remaining = deadline - loop.time()
            if remaining <= 0:
                return
            await asyncio.sleep(min(0.1, remaining))

    async def _sleep_during_net_wait(self, delay_seconds: int) -> None:
        await self._sleep_with_mailbox_activity(delay_seconds)

    async def _sleep_during_pacing_delay(self, delay_seconds: int) -> None:
        await self._sleep_with_mailbox_activity(delay_seconds)

    async def _handle_net_wait_recovery(
        self,
        result: ExecutionCycleResult,
        *,
        mode: Literal["once", "daemon"],
    ) -> bool:
        if mode != "daemon" or result.final_status is not ExecutionStatus.NET_WAIT:
            return False
        if result.run_id is None or result.transition_history_path is None:
            return False

        policy = OutagePolicySnapshot.from_config(self.loaded.config)
        if not policy.enabled:
            return False

        try:
            trigger = OutageTrigger.from_history(result.transition_history_path)
        except OutagePolicyError:
            return False

        history = TransitionHistoryStore(
            result.transition_history_path,
            run_id=result.run_id,
            provenance=self.execution_plane.runtime_provenance,
        )
        failing_result = result.stage_results[-1] if result.stage_results else None
        stage_label = trigger.stage.value.title()
        diagnostics_dir = result.diagnostics_dir or self.execution_plane._create_blocker_bundle(
            result.run_id,
            stage_label,
            trigger.preflight.reason,
            failing_result,
        )
        attempt_number = 1
        wait_seconds = policy.wait_initial_seconds

        while not self.stop_requested and not self.paused:
            probe = await asyncio.to_thread(self._outage_probe.check, policy)
            attempt = OutageAttempt(
                timestamp=datetime.now(timezone.utc),
                attempt=attempt_number,
                wait_seconds=wait_seconds,
                probe=probe,
            )
            decision = evaluate_outage_attempt(policy, attempt)
            current_status = self.execution_plane.status_store.read()
            if not isinstance(current_status, ExecutionStatus):
                raise ControlError("execution plane requires execution status markers")

            next_status = current_status
            active_task_after = trigger.task_id
            if decision.action is OutageAction.RESUME:
                next_status = ExecutionStatus.IDLE
            elif decision.action is OutageAction.ROUTE_TO_BLOCKER:
                next_status = ExecutionStatus.BLOCKED
            elif decision.action is OutageAction.ROUTE_TO_INCIDENT:
                next_status = ExecutionStatus.IDLE
                active_task_after = None

            record = outage_policy_record(
                trigger=trigger,
                policy=policy,
                attempt=attempt,
                decision=decision,
                transition_history_count=history.record_count,
                current_status=current_status,
            )
            self._append_outage_policy_record(
                history,
                trigger=trigger,
                record=record,
                status_before=current_status,
                status_after=next_status,
                active_task_after=active_task_after,
            )
            append_outage_attempt_log(
                diagnostics_dir,
                trigger=trigger,
                policy=policy,
                attempt=attempt,
                decision=decision,
            )

            if decision.action is OutageAction.RESUME:
                if current_status is not ExecutionStatus.IDLE:
                    self.execution_plane.status_store.transition(ExecutionStatus.IDLE)
                self._write_state(process_running=True, mode=mode)
                return True

            if decision.action is OutageAction.ROUTE_TO_INCIDENT:
                active_task = self.execution_plane.queue.active_task()
                if active_task is None:
                    diagnostics_dir = self.execution_plane.route_net_wait_to_blocker(
                        None,
                        run_id=result.run_id,
                        stage_label=stage_label,
                        reason=f"{decision.reason}; no active task remained for incident routing",
                        failing_result=failing_result,
                        diagnostics_dir=diagnostics_dir,
                    )
                    self.event_bus.emit(
                        EventType.STAGE_FAILED,
                        source=EventSource.EXECUTION,
                        payload={
                            "run_id": result.run_id,
                            "stage": trigger.stage.value,
                            "status": ExecutionStatus.BLOCKED.value,
                            "policy_outcome": decision.policy_decision.value,
                            "policy_reason": decision.reason,
                            "diagnostics_dir": diagnostics_dir,
                        },
                    )
                    self.paused = True
                    self.pause_reason = "net_wait_route_to_blocker"
                    self.pause_run_id = result.run_id
                    self.event_bus.emit(
                        EventType.ENGINE_PAUSED,
                        source=EventSource.EXECUTION,
                        payload={"run_id": result.run_id, "reason": "net_wait_route_to_blocker"},
                    )
                    self._write_state(process_running=True, mode=mode)
                    return False

                quarantined_task, diagnostics_dir, research_handoff = self.execution_plane.route_net_wait_to_incident(
                    active_task,
                    run_id=result.run_id,
                    stage_label=stage_label,
                    reason=decision.reason,
                    failing_result=failing_result,
                    diagnostics_dir=diagnostics_dir,
                )
                self.event_bus.emit(
                    EventType.TASK_QUARANTINED,
                    source=EventSource.EXECUTION,
                    payload={
                        "task_id": quarantined_task.task_id,
                        "title": quarantined_task.title,
                        "diagnostics_dir": diagnostics_dir,
                        "handoff_id": (
                            None if research_handoff is None else research_handoff.handoff_id
                        ),
                    },
                )
                self.event_bus.emit(
                    EventType.NEEDS_RESEARCH,
                    source=EventSource.EXECUTION,
                    payload={
                        "task_id": quarantined_task.task_id,
                        "title": quarantined_task.title,
                        "run_id": result.run_id,
                        **(
                            {"handoff": research_handoff.model_dump(mode="json")}
                            if research_handoff is not None
                            else {}
                        ),
                    },
                )
                self._write_state(process_running=True, mode=mode)
                return False

            if decision.action is OutageAction.ROUTE_TO_BLOCKER:
                active_task = self.execution_plane.queue.active_task()
                diagnostics_dir = self.execution_plane.route_net_wait_to_blocker(
                    active_task,
                    run_id=result.run_id,
                    stage_label=stage_label,
                    reason=decision.reason,
                    failing_result=failing_result,
                    diagnostics_dir=diagnostics_dir,
                )
                payload: dict[str, object] = {
                    "run_id": result.run_id,
                    "stage": trigger.stage.value,
                    "status": ExecutionStatus.BLOCKED.value,
                    "policy_outcome": decision.policy_decision.value,
                    "policy_reason": decision.reason,
                    "diagnostics_dir": diagnostics_dir,
                }
                if active_task is not None:
                    payload["task_id"] = active_task.task_id
                    payload["title"] = active_task.title
                self.event_bus.emit(
                    EventType.STAGE_FAILED,
                    source=EventSource.EXECUTION,
                    payload=payload,
                )
                self.paused = True
                self.pause_reason = "net_wait_route_to_blocker"
                self.pause_run_id = result.run_id
                self.event_bus.emit(
                    EventType.ENGINE_PAUSED,
                    source=EventSource.EXECUTION,
                    payload={"run_id": result.run_id, "reason": "net_wait_route_to_blocker"},
                )
                self._write_state(process_running=True, mode=mode)
                return False

            await self._sleep_during_net_wait(decision.next_wait_seconds or 0)
            if self.stop_requested or self.paused:
                self._write_state(process_running=True, mode=mode)
                return False
            if policy.max_probes > 0 and attempt_number >= policy.max_probes and policy.selected_route() is OutageRoute.PAUSE_RESUME:
                attempt_number = 1
                wait_seconds = policy.wait_initial_seconds
                continue
            attempt_number += 1
            if wait_seconds > 0:
                wait_seconds = min(max(wait_seconds * 2, policy.wait_initial_seconds), policy.wait_max_seconds)
            else:
                wait_seconds = min(policy.wait_initial_seconds, policy.wait_max_seconds)

        self._write_state(process_running=True, mode=mode)
        return False

    async def _run_cycle(self) -> ExecutionCycleResult | None:
        try:
            result = await asyncio.to_thread(self.execution_plane.run_once)
        except Exception as exc:  # noqa: BLE001 - supervisor owns lifecycle failures here
            self._rollback_active_config(str(exc))
            if not getattr(exc, "_millrace_stage_failed_emitted", False):
                self.event_bus.emit(
                    EventType.STAGE_FAILED,
                    source=EventSource.EXECUTION,
                    payload={"error": str(exc)},
                )
            self.stop_requested = True
            return None
        self._clear_rollback_guard()
        self._emit_cycle_events(result)
        self._request_stop_if_completion_honored()
        return result

    async def _run(self, *, mode: Literal["once", "daemon"]) -> RuntimeState:
        self.started_at = datetime.now(timezone.utc)
        self.paused = False
        self.pause_reason = None
        self.pause_run_id = None
        self.stop_requested = False
        self.input_queue = asyncio.Queue()
        self.file_watcher = self._build_file_watcher()
        if mode == "daemon" and self.file_watcher.mode == "watch":
            self.file_watcher.start()
        self.event_bus.emit(
            EventType.ENGINE_STARTED,
            source=EventSource.ENGINE,
            payload={"mode": mode, "config_hash": config_hash(self.loaded.config)},
        )
        self._consume_research_recovery_latch(trigger="engine_start")
        execution_queue = TaskQueue(self.paths)
        had_execution_work_before_research_sync = (
            execution_queue.active_task() is not None or execution_queue.peek_next() is not None
        )
        startup_research_dispatch = self._sync_ready_research_dispatch(trigger="engine-start")
        self._write_state(process_running=True, mode=mode)

        try:
            if mode == "once":
                if self._apply_pending_config_if_due(ConfigApplyBoundary.CYCLE_BOUNDARY):
                    await self._restart_file_watcher()
                skip_execution_cycle = (
                    startup_research_dispatch is not None and not had_execution_work_before_research_sync
                )
                if not skip_execution_cycle:
                    result = await self._run_cycle()
                    if result is not None and result.pause_requested:
                        self.paused = True
                        self.pause_reason = "usage_budget_threshold"
                        self.pause_run_id = result.run_id
                        self.event_bus.emit(
                            EventType.ENGINE_PAUSED,
                            source=EventSource.EXECUTION,
                            payload={
                                "run_id": result.run_id,
                                "reason": "usage_budget_threshold",
                                "policy_reason": result.pause_reason,
                            },
                        )
                        self._write_state(process_running=True, mode=mode)
                self.stop_requested = True
            else:
                while not self.stop_requested:
                    await self._drain_input_queue()
                    await self._ingest_poll_fallback_events()
                    await self.mailbox_processor.process_mailbox()
                    if self._apply_pending_config_if_due(ConfigApplyBoundary.CYCLE_BOUNDARY):
                        await self._restart_file_watcher()
                    self._sync_ready_research_dispatch(trigger="daemon-loop")
                    self._write_state(process_running=True, mode=mode)
                    if self.stop_requested:
                        break
                    if not self.paused:
                        result = await self._run_cycle()
                        self._write_state(process_running=True, mode=mode)
                        if self.stop_requested:
                            break
                        if result is not None and result.pause_requested:
                            self.paused = True
                            self.pause_reason = "usage_budget_threshold"
                            self.pause_run_id = result.run_id
                            self.event_bus.emit(
                                EventType.ENGINE_PAUSED,
                                source=EventSource.EXECUTION,
                                payload={
                                    "run_id": result.run_id,
                                    "reason": "usage_budget_threshold",
                                    "policy_reason": result.pause_reason,
                                },
                            )
                            self._write_state(process_running=True, mode=mode)
                            continue
                        if result is not None and await self._handle_net_wait_recovery(result, mode=mode):
                            continue
                        if result is not None and result.pacing_delay_seconds > 0:
                            await self._sleep_during_pacing_delay(result.pacing_delay_seconds)
                            if self.stop_requested:
                                break
                            continue
                    await self._wait_for_wakeup()
        finally:
            if self.file_watcher is not None:
                self.file_watcher.stop()
            self.research_plane.shutdown()
            final_state = self._write_state(process_running=False, mode=mode)
            self.event_bus.emit(
                EventType.ENGINE_STOPPED,
                source=EventSource.ENGINE,
                payload={"mode": mode, "paused": self.paused},
            )
        return final_state

    def start(self, *, daemon: bool = False, once: bool = False) -> RuntimeState:
        """Run the supervisor in foreground once or daemon mode."""

        if daemon and once:
            raise ControlError("start may use only one of daemon or once")
        mode: Literal["once", "daemon"] = "daemon" if daemon else "once"
        return asyncio.run(self._run(mode=mode))
