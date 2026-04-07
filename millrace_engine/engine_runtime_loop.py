"""Engine runtime-loop ownership seam."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from .adapters.file_watcher import FileWatcherAdapter, RuntimeInputEvent, RuntimeInputKind
from .config import ConfigApplyBoundary
from .contracts import CompletionDecision, ExecutionStatus
from .control_common import ControlError
from .control_models import RuntimeState
from .control_reports import config_hash, decision_report_paths
from .events import EventSource, EventType
from .planes.execution import ExecutionCycleResult
from .policies import (
    OutageAction,
    OutageAttempt,
    OutagePolicyError,
    OutagePolicySnapshot,
    OutageRoute,
    OutageTrigger,
    append_outage_attempt_log,
    evaluate_outage_attempt,
    outage_policy_record,
)
from .provenance import BoundExecutionParameters, TransitionHistoryStore
from .queue import TaskQueue

if TYPE_CHECKING:
    from .engine import MillraceEngine


class EngineRuntimeLoop:
    """Own watcher lifecycle and once/daemon runtime sequencing."""

    def __init__(self, engine: MillraceEngine) -> None:
        self.engine = engine
        self.input_queue: asyncio.Queue[RuntimeInputEvent] | None = None
        self.file_watcher: FileWatcherAdapter | None = None

    def build_file_watcher(self) -> FileWatcherAdapter:
        if self.input_queue is None:
            raise RuntimeError("input queue must exist before building the file watcher")
        return FileWatcherAdapter(
            self.engine.paths,
            emit=self.enqueue_input_event,
            config_path=self.engine.config_path,
            watch_roots=self.engine.loaded.config.watchers.roots,
            mode=self.engine.loaded.config.engine.idle_mode,
            debounce_seconds=self.engine.loaded.config.watchers.debounce_seconds,
            loop=asyncio.get_running_loop(),
        )

    def enqueue_input_event(self, event: RuntimeInputEvent) -> None:
        if self.input_queue is None:
            return
        self.input_queue.put_nowait(event)

    def request_stop_if_completion_honored(self) -> bool:
        marker_path = self.engine.paths.agents_dir / "AUTONOMY_COMPLETE"
        if not marker_path.exists() or not marker_path.is_file():
            return False
        _, completion_decision_path = decision_report_paths(self.engine.paths)
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
        self.engine.stop_requested = True
        return True

    async def reload_config_from_disk(self, *, trigger_path: Path | None = None) -> bool:
        operation_applied, restart_watcher = await self.engine.config_coordinator.reload_config_from_disk(
            trigger_path=trigger_path
        )
        if restart_watcher:
            await self.restart_file_watcher()
        return operation_applied

    async def handle_input_event(self, event: RuntimeInputEvent) -> None:
        if event.kind is RuntimeInputKind.BACKLOG_CHANGED:
            self.engine._consume_research_recovery_latch(trigger="backlog_changed", path=event.path)
            return
        if event.kind is RuntimeInputKind.CONFIG_CHANGED:
            await self.reload_config_from_disk(trigger_path=event.path)
            return
        if event.kind is RuntimeInputKind.CONTROL_COMMAND_AVAILABLE:
            await self.engine.mailbox_processor.process_mailbox()
            return
        if event.kind is RuntimeInputKind.IDEA_SUBMITTED:
            self.engine.event_bus.emit(
                EventType.IDEA_SUBMITTED,
                source=EventSource.ADAPTER,
                payload={"path": event.path},
            )
            return
        if event.kind is RuntimeInputKind.STOP_AUTONOMY:
            self.engine.stop_requested = True
            return
        if event.kind is RuntimeInputKind.AUTONOMY_COMPLETE:
            self.request_stop_if_completion_honored()

    async def drain_input_queue(self) -> None:
        if self.input_queue is None:
            return
        while True:
            try:
                event = self.input_queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            try:
                await self.handle_input_event(event)
            finally:
                self.input_queue.task_done()

    async def restart_file_watcher(self) -> None:
        if self.file_watcher is not None:
            self.file_watcher.stop()
        if self.input_queue is None:
            return
        self.file_watcher = self.build_file_watcher()
        if self.file_watcher.mode == "watch":
            self.file_watcher.start()

    async def ingest_poll_fallback_events(self) -> None:
        if self.file_watcher is None or self.file_watcher.mode != "poll":
            return
        for event in self.file_watcher.poll_once():
            await self.handle_input_event(event)

    async def wait_for_wakeup(self) -> None:
        timeout = self.engine.loaded.config.engine.poll_interval_seconds
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
            await self.handle_input_event(event)
        finally:
            self.input_queue.task_done()
        await self.drain_input_queue()

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

    async def sleep_with_mailbox_activity(self, delay_seconds: int) -> None:
        if delay_seconds <= 0:
            await self.drain_input_queue()
            await self.ingest_poll_fallback_events()
            await self.engine.mailbox_processor.process_mailbox()
            return
        loop = asyncio.get_running_loop()
        deadline = loop.time() + delay_seconds
        while not self.engine.stop_requested and not self.engine.paused:
            await self.drain_input_queue()
            await self.ingest_poll_fallback_events()
            await self.engine.mailbox_processor.process_mailbox()
            if self.engine.stop_requested or self.engine.paused:
                return
            remaining = deadline - loop.time()
            if remaining <= 0:
                return
            await asyncio.sleep(min(0.1, remaining))

    async def handle_net_wait_recovery(
        self,
        result: ExecutionCycleResult,
        *,
        mode: Literal["once", "daemon"],
    ) -> bool:
        if mode != "daemon" or result.final_status is not ExecutionStatus.NET_WAIT:
            return False
        if result.run_id is None or result.transition_history_path is None:
            return False

        policy = OutagePolicySnapshot.from_config(self.engine.loaded.config)
        if not policy.enabled:
            return False

        try:
            trigger = OutageTrigger.from_history(result.transition_history_path)
        except OutagePolicyError:
            return False

        history = TransitionHistoryStore(
            result.transition_history_path,
            run_id=result.run_id,
            provenance=self.engine.execution_plane.runtime_provenance,
        )
        failing_result = result.stage_results[-1] if result.stage_results else None
        stage_label = trigger.stage.value.title()
        diagnostics_dir = result.diagnostics_dir or self.engine.execution_plane._create_blocker_bundle(
            result.run_id,
            stage_label,
            trigger.preflight.reason,
            failing_result,
        )
        attempt_number = 1
        wait_seconds = policy.wait_initial_seconds

        while not self.engine.stop_requested and not self.engine.paused:
            probe = await asyncio.to_thread(self.engine._outage_probe.check, policy)
            attempt = OutageAttempt(
                timestamp=datetime.now(timezone.utc),
                attempt=attempt_number,
                wait_seconds=wait_seconds,
                probe=probe,
            )
            decision = evaluate_outage_attempt(policy, attempt)
            current_status = self.engine.execution_plane.status_store.read()
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
                    self.engine.execution_plane.status_store.transition(ExecutionStatus.IDLE)
                self.engine._write_state(process_running=True, mode=mode)
                return True

            if decision.action is OutageAction.ROUTE_TO_INCIDENT:
                active_task = self.engine.execution_plane.queue.active_task()
                if active_task is None:
                    diagnostics_dir = self.engine.execution_plane.route_net_wait_to_blocker(
                        None,
                        run_id=result.run_id,
                        stage_label=stage_label,
                        reason=f"{decision.reason}; no active task remained for incident routing",
                        failing_result=failing_result,
                        diagnostics_dir=diagnostics_dir,
                    )
                    self.engine.event_bus.emit(
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
                    self.engine.paused = True
                    self.engine.pause_reason = "net_wait_route_to_blocker"
                    self.engine.pause_run_id = result.run_id
                    self.engine.event_bus.emit(
                        EventType.ENGINE_PAUSED,
                        source=EventSource.EXECUTION,
                        payload={"run_id": result.run_id, "reason": "net_wait_route_to_blocker"},
                    )
                    self.engine._write_state(process_running=True, mode=mode)
                    return False

                quarantined_task, diagnostics_dir, research_handoff = self.engine.execution_plane.route_net_wait_to_incident(
                    active_task,
                    run_id=result.run_id,
                    stage_label=stage_label,
                    reason=decision.reason,
                    failing_result=failing_result,
                    diagnostics_dir=diagnostics_dir,
                )
                self.engine.event_bus.emit(
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
                self.engine.event_bus.emit(
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
                self.engine._write_state(process_running=True, mode=mode)
                return False

            if decision.action is OutageAction.ROUTE_TO_BLOCKER:
                active_task = self.engine.execution_plane.queue.active_task()
                diagnostics_dir = self.engine.execution_plane.route_net_wait_to_blocker(
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
                self.engine.event_bus.emit(
                    EventType.STAGE_FAILED,
                    source=EventSource.EXECUTION,
                    payload=payload,
                )
                self.engine.paused = True
                self.engine.pause_reason = "net_wait_route_to_blocker"
                self.engine.pause_run_id = result.run_id
                self.engine.event_bus.emit(
                    EventType.ENGINE_PAUSED,
                    source=EventSource.EXECUTION,
                    payload={"run_id": result.run_id, "reason": "net_wait_route_to_blocker"},
                )
                self.engine._write_state(process_running=True, mode=mode)
                return False

            await self.sleep_with_mailbox_activity(decision.next_wait_seconds or 0)
            if self.engine.stop_requested or self.engine.paused:
                self.engine._write_state(process_running=True, mode=mode)
                return False
            if (
                policy.max_probes > 0
                and attempt_number >= policy.max_probes
                and policy.selected_route() is OutageRoute.PAUSE_RESUME
            ):
                attempt_number = 1
                wait_seconds = policy.wait_initial_seconds
                continue
            attempt_number += 1
            if wait_seconds > 0:
                wait_seconds = min(max(wait_seconds * 2, policy.wait_initial_seconds), policy.wait_max_seconds)
            else:
                wait_seconds = min(policy.wait_initial_seconds, policy.wait_max_seconds)

        self.engine._write_state(process_running=True, mode=mode)
        return False

    def emit_cycle_events(self, result: ExecutionCycleResult) -> None:
        if result.archived_task is not None:
            self.engine.event_bus.emit(
                EventType.TASK_ARCHIVED,
                source=EventSource.EXECUTION,
                payload={"task_id": result.archived_task.task_id, "title": result.archived_task.title},
            )
        if result.quarantined_task is not None:
            self.engine.event_bus.emit(
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
            self.engine.event_bus.emit(
                EventType.NEEDS_RESEARCH,
                source=EventSource.EXECUTION,
                payload=payload,
            )
        if result.update_only:
            self.engine.event_bus.emit(
                EventType.BACKLOG_EMPTY_AUDIT,
                source=EventSource.EXECUTION,
                payload={"backlog_depth": 0},
            )

    async def run_cycle(self) -> ExecutionCycleResult | None:
        try:
            result = await asyncio.to_thread(self.engine.execution_plane.run_once)
        except Exception as exc:  # noqa: BLE001 - supervisor owns lifecycle failures here
            self.engine._rollback_active_config(str(exc))
            if not getattr(exc, "_millrace_stage_failed_emitted", False):
                self.engine.event_bus.emit(
                    EventType.STAGE_FAILED,
                    source=EventSource.EXECUTION,
                    payload={"error": str(exc)},
                )
            self.engine.stop_requested = True
            return None
        self.engine._clear_rollback_guard()
        self.emit_cycle_events(result)
        self.request_stop_if_completion_honored()
        return result

    async def run(self, *, mode: Literal["once", "daemon"]) -> RuntimeState:
        self.engine.started_at = datetime.now(timezone.utc)
        self.engine.paused = False
        self.engine.pause_reason = None
        self.engine.pause_run_id = None
        self.engine.stop_requested = False
        self.input_queue = asyncio.Queue()
        self.file_watcher = self.build_file_watcher()
        if mode == "daemon" and self.file_watcher.mode == "watch":
            self.file_watcher.start()
        self.engine.event_bus.emit(
            EventType.ENGINE_STARTED,
            source=EventSource.ENGINE,
            payload={"mode": mode, "config_hash": config_hash(self.engine.loaded.config)},
        )
        self.engine._consume_research_recovery_latch(trigger="engine_start")
        execution_queue = TaskQueue(self.engine.paths)
        had_execution_work_before_research_sync = (
            execution_queue.active_task() is not None or execution_queue.peek_next() is not None
        )
        startup_research_dispatch = self.engine._sync_ready_research_dispatch(trigger="engine-start")
        self.engine._write_state(process_running=True, mode=mode)

        try:
            if mode == "once":
                if self.engine._apply_pending_config_if_due(ConfigApplyBoundary.CYCLE_BOUNDARY):
                    await self.restart_file_watcher()
                skip_execution_cycle = (
                    startup_research_dispatch is not None and not had_execution_work_before_research_sync
                )
                if not skip_execution_cycle:
                    result = await self.run_cycle()
                    if result is not None and result.pause_requested:
                        self.engine.paused = True
                        self.engine.pause_reason = "usage_budget_threshold"
                        self.engine.pause_run_id = result.run_id
                        self.engine.event_bus.emit(
                            EventType.ENGINE_PAUSED,
                            source=EventSource.EXECUTION,
                            payload={
                                "run_id": result.run_id,
                                "reason": "usage_budget_threshold",
                                "policy_reason": result.pause_reason,
                            },
                        )
                        self.engine._write_state(process_running=True, mode=mode)
                self.engine.stop_requested = True
            else:
                while not self.engine.stop_requested:
                    await self.drain_input_queue()
                    await self.ingest_poll_fallback_events()
                    await self.engine.mailbox_processor.process_mailbox()
                    if self.engine._apply_pending_config_if_due(ConfigApplyBoundary.CYCLE_BOUNDARY):
                        await self.restart_file_watcher()
                    self.engine._sync_ready_research_dispatch(trigger="daemon-loop")
                    self.engine._write_state(process_running=True, mode=mode)
                    if self.engine.stop_requested:
                        break
                    if not self.engine.paused:
                        result = await self.run_cycle()
                        self.engine._write_state(process_running=True, mode=mode)
                        if self.engine.stop_requested:
                            break
                        if result is not None and result.pause_requested:
                            self.engine.paused = True
                            self.engine.pause_reason = "usage_budget_threshold"
                            self.engine.pause_run_id = result.run_id
                            self.engine.event_bus.emit(
                                EventType.ENGINE_PAUSED,
                                source=EventSource.EXECUTION,
                                payload={
                                    "run_id": result.run_id,
                                    "reason": "usage_budget_threshold",
                                    "policy_reason": result.pause_reason,
                                },
                            )
                            self.engine._write_state(process_running=True, mode=mode)
                            continue
                        if result is not None and await self.handle_net_wait_recovery(result, mode=mode):
                            continue
                        if result is not None and result.pacing_delay_seconds > 0:
                            await self.sleep_with_mailbox_activity(result.pacing_delay_seconds)
                            if self.engine.stop_requested:
                                break
                            continue
                    await self.wait_for_wakeup()
        finally:
            if self.file_watcher is not None:
                self.file_watcher.stop()
            self.engine.research_plane.shutdown()
            final_state = self.engine._write_state(process_running=False, mode=mode)
            self.engine.event_bus.emit(
                EventType.ENGINE_STOPPED,
                source=EventSource.ENGINE,
                payload={"mode": mode, "paused": self.engine.paused},
            )
        return final_state
