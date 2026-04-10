"""Top-level runtime supervisor composition shell."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from .config import (
    ConfigApplyBoundary,
    build_runtime_paths,
    load_engine_config,
)
from .contracts import ResearchMode
from .control_common import ControlError
from .control_models import OperationResult, RuntimeState
from .control_reports import (
    build_live_runtime_state,
    write_runtime_state,
)
from .engine_config_coordinator import EngineConfigCoordinator, EngineConfigCoordinatorHooks
from .engine_mailbox_processor import EngineMailboxHooks, EngineMailboxProcessor
from .engine_runtime_loop import EngineRuntimeLoop
from .events import EventBus, EventSource, EventType, HistorySubscriber, JsonlEventSubscriber
from .planes.base import StageCommandMap
from .planes.execution import ExecutionPlane
from .planes.research import ResearchPlane
from .policies import DefaultOutageProbe, OutageProbe, TransportProbe
from .queue import TaskQueue, load_research_recovery_latch
from .research.audit import AuditExecutionError
from .research.dispatcher import ResearchDispatchError
from .research.governance import sync_progress_watchdog
from .research.incidents import IncidentExecutionError


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
        initial_loaded = load_engine_config(self.config_path)
        initial_paths = build_runtime_paths(initial_loaded.config)
        self.execution_plane = ExecutionPlane(
            initial_loaded.config,
            initial_paths,
            stage_commands=self.stage_commands,
            before_stage=self._before_stage_boundary,
            emit_event=lambda event_type, payload: self.event_bus.emit(
                event_type,
                source=EventSource.EXECUTION,
                payload=payload,
            ),
            transport_probe=self._transport_probe,
        )
        self.research_plane = ResearchPlane(initial_loaded.config, initial_paths)
        self.event_bus = EventBus(
            [
                JsonlEventSubscriber(initial_paths),
                HistorySubscriber(initial_paths),
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
        self.config_coordinator = EngineConfigCoordinator(
            config_path=self.config_path,
            initial_loaded=initial_loaded,
            initial_paths=initial_paths,
            hooks=EngineConfigCoordinatorHooks(
                emit_event=lambda event_type, source, payload: self.event_bus.emit(
                    event_type,
                    source=source,
                    payload=payload,
                ),
                install_loaded_config=self._install_loaded_runtime,
                sync_ready_research_dispatch=lambda trigger: self._sync_ready_research_dispatch(
                    trigger=trigger
                ),
            ),
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
        self.runtime_loop = EngineRuntimeLoop(self)
        self.started_at: datetime | None = None
        self.paused = False
        self.pause_reason: str | None = None
        self.pause_run_id: str | None = None
        self.stop_requested = False

    @property
    def state_path(self) -> Path:
        return self.paths.runtime_dir / "state.json"

    @property
    def loaded(self):
        return self.config_coordinator.loaded

    @property
    def paths(self):
        return self.config_coordinator.paths

    @property
    def pending_loaded(self):
        return self.config_coordinator.pending_loaded

    @property
    def previous_loaded(self):
        return self.config_coordinator.previous_loaded

    @property
    def pending_boundary(self):
        return self.config_coordinator.pending_boundary

    @property
    def pending_changed_fields(self):
        return self.config_coordinator.pending_changed_fields

    @property
    def rollback_armed(self):
        return self.config_coordinator.rollback_armed

    def _set_stop_requested(self, requested: bool) -> None:
        self.stop_requested = requested

    def _set_pause_state(self, paused: bool, reason: str | None, run_id: str | None) -> None:
        self.paused = paused
        self.pause_reason = reason
        self.pause_run_id = run_id

    def _install_loaded_runtime(self, loaded, paths) -> None:
        self.execution_plane.reconfigure(loaded.config, paths)
        self.research_plane.reconfigure(loaded.config, paths)

    def _sync_ready_research_dispatch(self, *, trigger: str):
        if self.loaded.config.research.mode is ResearchMode.STUB:
            return None
        if self.research_plane.snapshot_state().checkpoint is None:
            thawed = self._consume_research_recovery_latch(trigger=f"research_sync:{trigger}")
            if thawed > 0:
                self.runtime_loop.request_stop_if_completion_honored()
                return None
        try:
            dispatch = self.research_plane.sync_runtime(trigger=trigger)
        except (ResearchDispatchError, IncidentExecutionError, AuditExecutionError):
            return None
        self.runtime_loop.request_stop_if_completion_honored()
        self._consume_research_recovery_latch(trigger=f"research_sync:{trigger}")
        return dispatch

    def _snapshot(self, *, process_running: bool, mode: Literal["once", "daemon"]) -> RuntimeState:
        config_state = self.config_coordinator.snapshot_state()
        return build_live_runtime_state(
            config_state.loaded,
            process_running=process_running,
            process_id=os.getpid() if process_running else None,
            paused=self.paused,
            pause_reason=self.pause_reason,
            pause_run_id=self.pause_run_id,
            started_at=self.started_at,
            mode=mode,
            pending_loaded=config_state.pending_loaded,
            previous_loaded=config_state.previous_loaded,
            pending_boundary=config_state.pending_boundary,
            pending_fields=config_state.pending_changed_fields,
            rollback_armed=config_state.rollback_armed,
        )

    def _write_state(self, *, process_running: bool, mode: Literal["once", "daemon"]) -> RuntimeState:
        state = self._snapshot(process_running=process_running, mode=mode)
        write_runtime_state(self.state_path, state)
        return state

    def _queue_or_apply_reloaded_config(
        self,
        loaded,
        *,
        command_id: str | None,
        key: str | None = None,
    ) -> tuple[OperationResult, bool]:
        return self.config_coordinator.queue_or_apply_reloaded_config(
            loaded,
            command_id=command_id,
            key=key,
        )

    def _apply_pending_config_if_due(
        self,
        boundary: ConfigApplyBoundary,
        *,
        command_id: str | None = None,
    ) -> bool:
        return self.config_coordinator.apply_pending_config_if_due(
            boundary,
            command_id=command_id,
        )

    def _clear_rollback_guard(self) -> None:
        self.config_coordinator.clear_rollback_guard()

    def _rollback_active_config(self, reason: str) -> bool:
        return self.config_coordinator.rollback_active_config(reason)

    async def _reload_config_from_disk(self, *, trigger_path: Path | None = None) -> bool:
        return await self.runtime_loop.reload_config_from_disk(trigger_path=trigger_path)

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

    def _before_stage_boundary(self, stage: object) -> None:
        del stage
        self.mailbox_processor.process_config_mailbox_between_stages()
        self._apply_pending_config_if_due(ConfigApplyBoundary.STAGE_BOUNDARY)

    async def _restart_file_watcher(self) -> None:
        await self.runtime_loop.restart_file_watcher()

    def start(self, *, daemon: bool = False, once: bool = False) -> RuntimeState:
        """Run the supervisor in foreground once or daemon mode."""

        if daemon and once:
            raise ControlError("start may use only one of daemon or once")
        mode: Literal["once", "daemon"] = "daemon" if daemon else "once"
        import asyncio

        return asyncio.run(self.runtime_loop.run(mode=mode))
