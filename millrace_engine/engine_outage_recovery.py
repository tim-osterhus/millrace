"""Owned outage-recovery helper for the runtime loop."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Literal, Protocol

from .contracts import ExecutionStatus
from .control_common import ControlError
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


class EngineRuntimeLoopOwner(Protocol):
    loaded: Any
    execution_plane: Any
    event_bus: Any
    _outage_probe: Any
    stop_requested: bool
    paused: bool
    pause_reason: str | None
    pause_run_id: str | None

    def _write_state(self, *, process_running: bool, mode: Literal["once", "daemon"]) -> Any: ...


class EngineOutageRecovery:
    """Handle daemon NET_WAIT recovery without bloating the runtime loop owner."""

    def __init__(self, engine: EngineRuntimeLoopOwner) -> None:
        self.engine = engine

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
        record: Any,
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

    async def handle_net_wait_recovery(
        self,
        result: ExecutionCycleResult,
        *,
        mode: Literal["once", "daemon"],
        sleep_with_mailbox_activity: Callable[[int], Awaitable[None]],
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

                quarantined_task, diagnostics_dir, research_handoff = (
                    self.engine.execution_plane.route_net_wait_to_incident(
                        active_task,
                        run_id=result.run_id,
                        stage_label=stage_label,
                        reason=decision.reason,
                        failing_result=failing_result,
                        diagnostics_dir=diagnostics_dir,
                    )
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

            await sleep_with_mailbox_activity(decision.next_wait_seconds or 0)
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
                wait_seconds = min(
                    max(wait_seconds * 2, policy.wait_initial_seconds),
                    policy.wait_max_seconds,
                )
            else:
                wait_seconds = min(policy.wait_initial_seconds, policy.wait_max_seconds)

        self.engine._write_state(process_running=True, mode=mode)
        return False
