"""Plane-concurrent daemon supervisor."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import model_validator

from millrace_ai.contracts import ActiveRunState, Plane, StageResultEnvelope
from millrace_ai.contracts.base import ContractModel
from millrace_ai.errors import WorkspaceStateError
from millrace_ai.events import write_runtime_event
from millrace_ai.router import RouterDecision
from millrace_ai.runners import RunnerRawResult, StageRunRequest, normalize_stage_result
from millrace_ai.state_store import save_snapshot

from .activation import activate_claim_for_plane, claim_next_work_item_for_plane
from .active_runs import active_run_for_plane, snapshot_with_active_run
from .error_recovery import schedule_post_stage_exception_recovery
from .learning_promotions import (
    apply_deferred_learning_promotions_if_safe,
    handle_learning_curator_promotion_boundary,
)
from .learning_triggers import enqueue_learning_requests_for_stage_result
from .plane_concurrency import can_dispatch_plane

if TYPE_CHECKING:
    from millrace_ai.runtime.engine import RuntimeEngine


_FOREGROUND_PLANES: tuple[Plane, ...] = (Plane.PLANNING, Plane.EXECUTION)
_DISPATCH_ORDER: tuple[Plane, ...] = (Plane.PLANNING, Plane.EXECUTION, Plane.LEARNING)


class StageWorkerOutcome(ContractModel):
    """Typed completion payload returned by an isolated stage worker."""

    plane: Plane
    run_id: str
    active_run: ActiveRunState
    request: StageRunRequest
    started_at: datetime
    completed_at: datetime
    raw_result: RunnerRawResult | None = None
    exception_type: str | None = None
    exception_message: str | None = None

    @model_validator(mode="after")
    def validate_worker_outcome(self) -> "StageWorkerOutcome":
        has_raw_result = self.raw_result is not None
        has_exception = self.exception_type is not None
        if has_raw_result == has_exception:
            raise ValueError("exactly one of raw_result or exception_type must be present")
        if self.plane is not self.active_run.plane:
            raise ValueError("worker outcome plane must match active run plane")
        if self.run_id != self.active_run.run_id:
            raise ValueError("worker outcome run_id must match active run run_id")
        if self.request.plane is not self.plane:
            raise ValueError("worker outcome request plane must match outcome plane")
        if self.request.run_id != self.run_id:
            raise ValueError("worker outcome request run_id must match outcome run_id")
        if has_exception and not self.exception_message:
            raise ValueError("exception worker outcomes require exception_message")
        return self


@dataclass(frozen=True, slots=True)
class StageCompletionOutcome:
    """Supervisor-owned result application outcome."""

    stage_result: StageResultEnvelope
    stage_result_path: Path
    router_decision: RouterDecision


class RuntimeDaemonSupervisor:
    """Own daemon dispatch, worker completion, and serialized runtime mutation."""

    def __init__(self, engine: RuntimeEngine) -> None:
        self.engine = engine
        self._tasks: dict[Plane, asyncio.Task[StageWorkerOutcome]] = {}

    @property
    def active_worker_planes(self) -> frozenset[Plane]:
        return frozenset(self._tasks)

    async def run_cycle(self) -> tuple[StageCompletionOutcome, ...]:
        """Run one daemon supervisor cycle."""

        completions = await self.drain_completed(wait=False)
        self._prepare_cycle()

        if self._stop_requested_and_drained():
            self._stop_runtime()
            return completions

        if not self._dispatch_blocked():
            await self.dispatch_ready_work(process_completed=False)

        if not completions and not self._tasks and not self._active_runs():
            self._emit_idle_cycle()
        return completions

    async def dispatch_ready_work(self, *, process_completed: bool = True) -> int:
        """Dispatch all currently eligible lanes and return the number started."""

        if process_completed:
            await self.drain_completed(wait=False)
            self._prepare_cycle()

        dispatched = 0
        for active_run in self._active_runs_in_dispatch_order():
            if active_run.plane not in self._tasks:
                self._start_worker(active_run)
                dispatched += 1

        if self._dispatch_blocked():
            return dispatched

        foreground_dispatched = await self._dispatch_foreground_lane()
        dispatched += foreground_dispatched

        learning_dispatched = await self._dispatch_claim_for_plane(Plane.LEARNING)
        dispatched += learning_dispatched
        return dispatched

    async def drain_completed(self, *, wait: bool = False) -> tuple[StageCompletionOutcome, ...]:
        """Apply completed worker outcomes on the supervisor path."""

        completions: list[StageCompletionOutcome] = []
        while self._tasks:
            done_planes = self._done_planes()
            if not done_planes and wait:
                await asyncio.wait(self._tasks.values(), return_when=asyncio.FIRST_COMPLETED)
                done_planes = self._done_planes()
            if not done_planes:
                break

            for plane in _sort_planes(done_planes):
                task = self._tasks.pop(plane)
                outcome = task.result()
                completions.append(apply_stage_completion(self.engine, outcome=outcome))

            if not wait:
                break
        return tuple(completions)

    async def wait_for_next_completion_or_timeout(self, timeout_seconds: float) -> None:
        """Wait until any active worker completes, or until the idle timeout elapses."""

        if self._tasks:
            await asyncio.wait(
                self._tasks.values(),
                timeout=timeout_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            return
        await asyncio.sleep(timeout_seconds)

    async def _dispatch_foreground_lane(self) -> int:
        for plane in _FOREGROUND_PLANES:
            dispatched = await self._dispatch_claim_for_plane(plane)
            if dispatched:
                return dispatched
        return await self._dispatch_completion_stage()

    async def _dispatch_claim_for_plane(self, plane: Plane) -> int:
        if not self._can_dispatch(plane):
            return 0
        claim = claim_next_work_item_for_plane(self.engine, plane)
        if claim is None:
            return 0
        try:
            active_run = activate_claim_for_plane(self.engine, claim, plane)
        except RuntimeError:
            return 0
        self._start_worker(active_run)
        return 1

    async def _dispatch_completion_stage(self) -> int:
        if not self._can_dispatch(Plane.PLANNING):
            return 0
        if self._pending_completion_exists():
            return 0
        target = self.engine._maybe_activate_completion_stage()
        if target is None:
            return 0
        assert self.engine.snapshot is not None
        active_run = active_run_for_plane(self.engine.snapshot, Plane.PLANNING)
        if active_run is None:
            return 0
        self._start_worker(active_run)
        return 1

    def _start_worker(self, active_run: ActiveRunState) -> None:
        if active_run.plane in self._tasks:
            return
        request = self._request_for_active_run(active_run)
        active_run = active_run.model_copy(
            update={"running_status_marker": request.running_status_marker}
        )
        assert self.engine.snapshot is not None
        self.engine.snapshot = snapshot_with_active_run(
            self.engine.snapshot,
            active_run,
            now=self.engine._now(),
            current_failure_class=self.engine.snapshot.current_failure_class,
        )
        self.engine._mark_active_stage_running(
            plane=request.plane,
            stage=request.stage,
            running_status_marker=request.running_status_marker,
            run_id=request.run_id,
        )
        _emit_stage_started(self.engine, request)
        self._tasks[active_run.plane] = asyncio.create_task(
            _run_stage_worker(self.engine, active_run=active_run, request=request)
        )

    def _request_for_active_run(self, active_run: ActiveRunState) -> StageRunRequest:
        stage_plan = self.engine._stage_plan_for(
            active_run.plane,
            active_run.stage,
            node_id=active_run.node_id,
        )
        if active_run.request_kind != "closure_target":
            return self.engine._build_stage_run_request(stage_plan)

        closure_target = self.engine._active_closure_target()
        if closure_target is None:
            raise WorkspaceStateError("completion stage is active without an open closure target")
        return self.engine._build_closure_target_stage_run_request(stage_plan, closure_target)

    def _prepare_cycle(self) -> None:
        if self.engine.snapshot is None or self.engine.counters is None or self.engine.compiled_plan is None:
            self.engine.startup()
        assert self.engine.snapshot is not None
        self.engine._drain_mailbox()
        self.engine._consume_watcher_events()
        self.engine._refresh_runtime_queue_depths()
        self.engine._evaluate_usage_governance()
        self.engine._run_reconciliation_if_needed()
        self.engine._refresh_runtime_queue_depths(process_running=True)

    def _dispatch_blocked(self) -> bool:
        assert self.engine.snapshot is not None
        return self.engine.snapshot.paused or self.engine.snapshot.stop_requested

    def _stop_requested_and_drained(self) -> bool:
        assert self.engine.snapshot is not None
        return self.engine.snapshot.stop_requested and not self._tasks and not self._active_runs()

    def _stop_runtime(self) -> None:
        self.engine._reset_runtime_to_idle(
            process_running=False,
            clear_stop_requested=True,
            clear_paused=True,
        )
        self.engine._close_watcher_session()
        self.engine._release_daemon_ownership_lock(force=False)
        write_runtime_event(self.engine.paths, event_type="runtime_tick_stopped")
        self.engine._emit_monitor_event("runtime_stopped", reason="stop_requested")

    def _emit_idle_cycle(self) -> None:
        assert self.engine.snapshot is not None
        save_snapshot(self.engine.paths, self.engine.snapshot)
        write_runtime_event(self.engine.paths, event_type="runtime_tick_idle")
        self.engine._emit_monitor_event("runtime_idle", reason="no_work")

    def _can_dispatch(self, plane: Plane) -> bool:
        assert self.engine.compiled_plan is not None
        return can_dispatch_plane(
            policy=self.engine.compiled_plan.concurrency_policy,
            active_planes=self._active_planes(),
            candidate=plane,
        )

    def _active_planes(self) -> frozenset[Plane]:
        return frozenset((*self._active_runs(), *self._tasks))

    def _active_runs(self) -> dict[Plane, ActiveRunState]:
        assert self.engine.snapshot is not None
        return dict(self.engine.snapshot.active_runs_by_plane)

    def _active_runs_in_dispatch_order(self) -> tuple[ActiveRunState, ...]:
        active_runs = self._active_runs()
        return tuple(
            active_runs[plane]
            for plane in _DISPATCH_ORDER
            if plane in active_runs and plane not in self._tasks
        )

    def _done_planes(self) -> tuple[Plane, ...]:
        return tuple(plane for plane, task in self._tasks.items() if task.done())

    def _pending_completion_exists(self) -> bool:
        return any(task.done() for task in self._tasks.values())


async def _run_stage_worker(
    engine: RuntimeEngine,
    *,
    active_run: ActiveRunState,
    request: StageRunRequest,
) -> StageWorkerOutcome:
    started_at = engine._now()
    try:
        raw_result = await asyncio.to_thread(engine.stage_runner, request)
    except Exception as exc:  # pragma: no cover - defensive path
        return StageWorkerOutcome(
            plane=active_run.plane,
            run_id=active_run.run_id,
            active_run=active_run,
            request=request,
            started_at=started_at,
            completed_at=engine._now(),
            exception_type=type(exc).__name__,
            exception_message=str(exc),
        )
    return StageWorkerOutcome(
        plane=active_run.plane,
        run_id=active_run.run_id,
        active_run=active_run,
        request=request,
        started_at=started_at,
        completed_at=engine._now(),
        raw_result=raw_result,
    )


def apply_stage_completion(
    engine: RuntimeEngine,
    *,
    outcome: StageWorkerOutcome,
) -> StageCompletionOutcome:
    """Apply one worker completion through existing runtime-owned mutation helpers."""

    raw_result = outcome.raw_result
    if raw_result is None:
        raw_result = engine._runner_failure_result(
            outcome.request,
            failure_class="runner_error",
            error=outcome.exception_message or outcome.exception_type or "runner error",
        )
    stage_result = normalize_stage_result(outcome.request, raw_result)
    _validate_stage_result_matches_active_run(outcome.active_run, stage_result)

    stage_result_path: Path | None = None
    router_decision: RouterDecision | None = None
    try:
        stage_result_path = engine._write_stage_result(outcome.request, stage_result)
        enqueue_learning_requests_for_stage_result(
            engine,
            stage_result=stage_result,
            stage_result_path=stage_result_path,
        )
        router_decision = engine._route_stage_result(stage_result)
        engine._write_plane_status(stage_result)
        engine._apply_router_decision(router_decision, stage_result)
        handle_learning_curator_promotion_boundary(engine, stage_result=stage_result)
        apply_deferred_learning_promotions_if_safe(engine)
    except Exception as exc:
        recovery_decision = schedule_post_stage_exception_recovery(
            engine,
            stage_result=stage_result,
            error=exc,
            router_decision=router_decision,
            stage_result_path=stage_result_path,
        )
        return StageCompletionOutcome(
            stage_result=stage_result,
            stage_result_path=stage_result_path
            or (engine.paths.logs_dir / f"{outcome.request.request_id}.stage_result.unavailable.json"),
            router_decision=recovery_decision,
        )

    assert stage_result_path is not None
    assert router_decision is not None
    assert engine.snapshot is not None
    engine.snapshot = engine.snapshot.model_copy(
        update={
            "last_terminal_result": stage_result.terminal_result,
            "last_stage_result_path": str(stage_result_path.relative_to(engine.paths.root)),
            "queue_depth_execution": engine._execution_queue_depth(),
            "queue_depth_planning": engine._planning_queue_depth(),
            "queue_depth_learning": engine._learning_queue_depth(),
            "updated_at": engine._now(),
        }
    )
    save_snapshot(engine.paths, engine.snapshot)
    _emit_stage_completed(engine, outcome.request, stage_result)
    _emit_router_decision(engine, outcome.request, stage_result, router_decision)
    engine._evaluate_usage_governance(
        stage_result=stage_result,
        stage_result_path=stage_result_path,
    )
    return StageCompletionOutcome(
        stage_result=stage_result,
        stage_result_path=stage_result_path,
        router_decision=router_decision,
    )


def _validate_stage_result_matches_active_run(
    active_run: ActiveRunState,
    stage_result: StageResultEnvelope,
) -> None:
    if stage_result.plane is not active_run.plane:
        raise ValueError("stage_result plane does not match active run plane")
    if stage_result.stage != active_run.stage:
        raise ValueError("stage_result stage does not match active run stage")
    if stage_result.node_id != active_run.node_id:
        raise ValueError("stage_result node_id does not match active run node_id")
    if stage_result.stage_kind_id != active_run.stage_kind_id:
        raise ValueError("stage_result stage_kind_id does not match active run stage_kind_id")
    if stage_result.run_id != active_run.run_id:
        raise ValueError("stage_result run_id does not match active run run_id")
    if active_run.request_kind == "closure_target":
        root_spec_id = stage_result.metadata.get("closure_target_root_spec_id")
        if root_spec_id != active_run.closure_target_root_spec_id:
            raise ValueError("stage_result closure target root does not match active run")
        return
    if stage_result.work_item_kind is not active_run.work_item_kind:
        raise ValueError("stage_result work item kind does not match active run")
    if stage_result.work_item_id != active_run.work_item_id:
        raise ValueError("stage_result work item id does not match active run")


def _emit_stage_started(engine: RuntimeEngine, request: StageRunRequest) -> None:
    engine._emit_monitor_event(
        "stage_started",
        plane=request.plane.value,
        stage=request.stage.value,
        node_id=request.node_id,
        stage_kind_id=request.stage_kind_id,
        run_id=request.run_id,
        work_item_kind=(request.active_work_item_kind.value if request.active_work_item_kind else None),
        work_item_id=request.active_work_item_id,
        status_marker=request.running_status_marker,
    )
    write_runtime_event(
        engine.paths,
        event_type="stage_started",
        data={
            "request_id": request.request_id,
            "stage": request.stage.value,
            "node_id": request.node_id,
            "stage_kind_id": request.stage_kind_id,
            "plane": request.plane.value,
            "run_id": request.run_id,
            "work_item_kind": (
                request.active_work_item_kind.value if request.active_work_item_kind else None
            ),
            "work_item_id": request.active_work_item_id,
            "troubleshoot_report_path": request.preferred_troubleshoot_report_path,
        },
    )


def _emit_stage_completed(
    engine: RuntimeEngine,
    request: StageRunRequest,
    stage_result: StageResultEnvelope,
) -> None:
    write_runtime_event(
        engine.paths,
        event_type="stage_completed",
        data={
            "request_id": request.request_id,
            "stage": stage_result.stage.value,
            "node_id": stage_result.node_id,
            "stage_kind_id": stage_result.stage_kind_id,
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
    engine._emit_monitor_event(
        "stage_completed",
        plane=stage_result.plane.value,
        stage=stage_result.stage.value,
        node_id=stage_result.node_id,
        stage_kind_id=stage_result.stage_kind_id,
        run_id=stage_result.run_id,
        work_item_kind=stage_result.work_item_kind.value,
        work_item_id=stage_result.work_item_id,
        terminal_result=stage_result.terminal_result.value,
        summary_status_marker=stage_result.summary_status_marker,
        started_at=stage_result.started_at.isoformat(),
        completed_at=stage_result.completed_at.isoformat(),
        duration_seconds=stage_result.duration_seconds,
        token_usage=(
            stage_result.token_usage.model_dump(mode="json")
            if stage_result.token_usage is not None
            else None
        ),
    )


def _emit_router_decision(
    engine: RuntimeEngine,
    request: StageRunRequest,
    stage_result: StageResultEnvelope,
    router_decision: RouterDecision,
) -> None:
    write_runtime_event(
        engine.paths,
        event_type="router_decision",
        data={
            "action": router_decision.action.value,
            "plane": stage_result.plane.value,
            "run_id": request.run_id,
            "work_item_kind": stage_result.work_item_kind.value,
            "work_item_id": stage_result.work_item_id,
            "stage": stage_result.stage.value,
            "node_id": stage_result.node_id,
            "stage_kind_id": stage_result.stage_kind_id,
            "terminal_result": stage_result.terminal_result.value,
            "failure_class": stage_result.metadata.get("failure_class"),
            "troubleshoot_report_path": (
                stage_result.report_artifact or request.preferred_troubleshoot_report_path
            ),
            "next_stage": router_decision.next_stage.value if router_decision.next_stage else None,
            "next_node_id": router_decision.next_node_id,
            "next_stage_kind_id": router_decision.next_stage_kind_id,
            "reason": router_decision.reason,
        },
    )
    engine._emit_monitor_event(
        "router_decision",
        action=router_decision.action.value,
        plane=stage_result.plane.value,
        run_id=stage_result.run_id,
        work_item_kind=stage_result.work_item_kind.value,
        work_item_id=stage_result.work_item_id,
        stage=stage_result.stage.value,
        node_id=stage_result.node_id,
        stage_kind_id=stage_result.stage_kind_id,
        terminal_result=stage_result.terminal_result.value,
        failure_class=stage_result.metadata.get("failure_class"),
        next_stage=router_decision.next_stage.value if router_decision.next_stage else None,
        next_node_id=router_decision.next_node_id,
        next_stage_kind_id=router_decision.next_stage_kind_id,
        reason=router_decision.reason,
    )


def _sort_planes(planes: tuple[Plane, ...]) -> tuple[Plane, ...]:
    return tuple(plane for plane in _DISPATCH_ORDER if plane in planes)


__all__ = [
    "RuntimeDaemonSupervisor",
    "StageCompletionOutcome",
    "StageWorkerOutcome",
    "apply_stage_completion",
]
