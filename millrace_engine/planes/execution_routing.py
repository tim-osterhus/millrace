"""Execution-plane frozen-plan routing helpers."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from ..compiler import FrozenLoopPlan, FrozenStagePlan, FrozenTransition
from ..contracts import ExecutionStatus, StageResult, StageType, TaskCard
from ..execution_nodes import execution_stage_type_for_node
from ..events import EventType
from ..stages.base import StageExecutionError

if TYPE_CHECKING:
    from ..config import EngineConfig
    from ..paths import RuntimePaths
    from ..queue import TaskQueue
    from ..status import StatusStore


class ExecutionRoutingPlane(Protocol):
    _active_frozen_plan: object | None
    config: EngineConfig
    queue: TaskQueue
    status_store: StatusStore
    paths: RuntimePaths

    def _builder_success_target(self) -> str: ...

    def _clear_active_quickfix_artifact(self) -> None: ...

    def _quarantine_task(
        self,
        task: TaskCard,
        *,
        run_id: str,
        stage_label: str,
        why: str,
        diagnostics_dir: Path,
        consult_result: StageResult | None,
    ) -> TaskCard: ...

    def _emit_event(self, event_type: EventType, payload: dict[str, Any] | None = None) -> None: ...

    def _run_stage(
        self,
        stage_type: StageType,
        task: TaskCard | None,
        run_id: str,
        *,
        node_id: str | None = None,
    ) -> StageResult: ...

    def _create_blocker_bundle(
        self,
        run_id: str,
        stage_label: str,
        why: str,
        failing_result: StageResult | None,
    ) -> Path: ...

    def _record_stage_transition(self, result: StageResult, **kwargs: object) -> None: ...


ExecutionOutcome = tuple[ExecutionStatus, TaskCard | None, TaskCard | None, Path | None, int]


def execution_plan(plane: ExecutionRoutingPlane) -> FrozenLoopPlan:
    """Return the active frozen execution plan for the current run."""

    if plane._active_frozen_plan is None or plane._active_frozen_plan.content.execution_plan is None:
        raise StageExecutionError("execution plane requires a frozen execution plan for this run")
    return plane._active_frozen_plan.content.execution_plan


def stage_plan(plane: ExecutionRoutingPlane, node_id: str) -> FrozenStagePlan:
    """Resolve one stage node from the active frozen execution plan."""

    for stage in execution_plan(plane).stages:
        if stage.node_id == node_id:
            return stage
    raise StageExecutionError(f"frozen execution plan is missing node {node_id}")


def stage_type_for_node(plane: ExecutionRoutingPlane, node_id: str) -> StageType:
    """Map one frozen-plan node id back to a public execution stage type."""

    del plane
    stage_type = execution_stage_type_for_node(node_id)
    if stage_type is None:
        raise StageExecutionError(
            f"frozen execution plan node {node_id} cannot map to a public execution stage"
        )
    return stage_type


def routing_facts(plane: ExecutionRoutingPlane) -> dict[str, object]:
    """Build the static routing facts used by frozen-plan conditions."""

    return {
        "builder_success_target": plane._builder_success_target(),
    }


def derive_stage_outcome(plane: ExecutionRoutingPlane, stage_plan_value: FrozenStagePlan, result_status: str) -> str:
    """Normalize one stage terminal status into a frozen-plan routing outcome."""

    del plane
    if result_status in stage_plan_value.success_statuses:
        return "success"
    lowered = result_status.strip().lower()
    if lowered == "needs_research" and "handoff" in stage_plan_value.routing_outcomes:
        return "handoff"
    if lowered in stage_plan_value.routing_outcomes:
        return lowered
    if "terminal_failure" in stage_plan_value.routing_outcomes:
        return "terminal_failure"
    raise StageExecutionError(
        f"stage {stage_plan_value.node_id} produced unsupported status {result_status} for the frozen plan"
    )


def condition_matches(
    plane: ExecutionRoutingPlane,
    transition: FrozenTransition,
    *,
    facts: dict[str, object],
    artifacts: tuple[Path, ...] = (),
) -> tuple[bool | None, dict[str, object]]:
    """Evaluate one frozen-plan condition against runtime facts and artifacts."""

    del plane
    condition = transition.condition
    if condition is None:
        return None, {}
    kind = str(condition.get("kind", "")).strip()
    if kind == "always":
        return True, {}
    if kind == "fact_equals":
        fact_name = str(condition.get("fact", "")).strip()
        expected = condition.get("value")
        actual = facts.get(fact_name)
        return actual == expected, {"fact": fact_name, "expected": expected, "actual": actual}
    if kind == "artifact_present":
        artifact_name = str(condition.get("artifact_name", "")).strip()
        present = any(path.name == artifact_name for path in artifacts)
        return present, {"artifact_name": artifact_name, "present": present}
    raise StageExecutionError(f"frozen transition {transition.edge_id} uses unsupported condition {kind!r}")


def select_transition(
    plane: ExecutionRoutingPlane,
    node_id: str,
    *,
    trigger_status: str,
    facts: dict[str, object],
    artifacts: tuple[Path, ...] = (),
) -> tuple[FrozenTransition, str, bool | None, dict[str, object]]:
    """Select the winning frozen-plan transition for one stage outcome."""

    current_stage_plan = stage_plan(plane, node_id)
    derived_outcome = derive_stage_outcome(plane, current_stage_plan, trigger_status)
    candidate_triggers = (derived_outcome, trigger_status)
    transitions = sorted(
        (
            transition
            for transition in execution_plan(plane).transitions
            if transition.from_node_id == node_id
            and any(trigger in transition.on_outcomes for trigger in candidate_triggers)
        ),
        key=lambda item: (item.priority, item.edge_id),
    )
    for transition in transitions:
        condition_result, condition_inputs = condition_matches(
            plane,
            transition,
            facts=facts,
            artifacts=artifacts,
        )
        if condition_result is False:
            continue
        return transition, derived_outcome, condition_result, condition_inputs
    raise StageExecutionError(
        f"frozen execution plan has no eligible transition for {node_id} on {trigger_status}"
    )


def apply_terminal_transition(
    plane: ExecutionRoutingPlane,
    transition: FrozenTransition,
    *,
    task: TaskCard,
    run_id: str,
    stage_results: list[StageResult],
    diagnostics_dir: Path | None = None,
    quickfix_attempts: int = 0,
) -> ExecutionOutcome:
    """Apply one frozen-plan terminal state and return the cycle outcome tuple."""

    terminal_state = next(
        (
            candidate
            for candidate in execution_plan(plane).terminal_states
            if candidate.terminal_state_id == transition.terminal_state_id
        ),
        None,
    )
    if terminal_state is None:
        raise StageExecutionError(
            f"frozen execution plan is missing terminal state {transition.terminal_state_id}"
        )
    if terminal_state.terminal_state_id == "idle":
        plane.queue.archive(task)
        plane.status_store.transition(ExecutionStatus.IDLE)
        plane._clear_active_quickfix_artifact()
        return ExecutionStatus.IDLE, task, None, diagnostics_dir, quickfix_attempts
    if terminal_state.terminal_state_id == "blocked":
        if plane.status_store.read() is not ExecutionStatus.BLOCKED:
            plane.status_store.transition(ExecutionStatus.BLOCKED)
        return ExecutionStatus.BLOCKED, None, None, diagnostics_dir, quickfix_attempts
    if terminal_state.terminal_state_id == "needs_research":
        consult_result = stage_results[-1] if stage_results else None
        why = "frozen plan routed consult to needs_research"
        if len(stage_results) >= 2:
            prior_result = stage_results[-2]
            why = (
                f"exit={prior_result.exit_code} status={prior_result.status} "
                f"(consult handoff status: {consult_result.status if consult_result is not None else 'n/a'})"
            )
        elif consult_result is not None:
            why = f"exit={consult_result.exit_code} status={consult_result.status}"
        quarantined = plane._quarantine_task(
            task,
            run_id=run_id,
            stage_label="Consult",
            why=why,
            diagnostics_dir=diagnostics_dir or plane.paths.diagnostics_dir,
            consult_result=consult_result,
        )
        return (
            ExecutionStatus.IDLE,
            None,
            quarantined,
            diagnostics_dir or plane.paths.diagnostics_dir,
            quickfix_attempts,
        )
    raise StageExecutionError(
        f"frozen execution plan terminal state {terminal_state.terminal_state_id} is not implemented in v1"
    )


def legacy_resume_completed_node(plane: ExecutionRoutingPlane, status: ExecutionStatus) -> str | None:
    """Map legacy completed statuses back to a frozen-plan node id."""

    del plane
    mapping = {
        ExecutionStatus.BUILDER_COMPLETE: "builder",
        ExecutionStatus.INTEGRATION_COMPLETE: "integration",
        ExecutionStatus.QA_COMPLETE: "qa",
        ExecutionStatus.TROUBLESHOOT_COMPLETE: "troubleshoot",
        ExecutionStatus.CONSULT_COMPLETE: "consult",
        ExecutionStatus.UPDATE_COMPLETE: "update",
        ExecutionStatus.LARGE_PLAN_COMPLETE: "large_plan",
        ExecutionStatus.LARGE_EXECUTE_COMPLETE: "large_execute",
        ExecutionStatus.LARGE_REASSESS_COMPLETE: "reassess",
        ExecutionStatus.LARGE_REFACTOR_COMPLETE: "refactor",
    }
    return mapping.get(status)


def run_frozen_plan(
    plane: ExecutionRoutingPlane,
    task: TaskCard,
    *,
    run_id: str,
    stage_results: list[StageResult],
    start_node_id: str,
    transition_reason_prefix: str,
    routing_mode: str,
) -> ExecutionOutcome:
    """Run the active frozen execution plan from one node until it reaches a terminal state."""

    node_id = start_node_id
    quickfix_attempts = 0
    diagnostics_dir: Path | None = None
    while True:
        if node_id == "hotfix":
            plane._mark_quickfix_artifact_active()
            next_attempt = quickfix_attempts + 1
            max_attempts = plane.config.execution.quickfix_max_attempts
            if next_attempt > max_attempts:
                plane._emit_event(
                    EventType.QUICKFIX_EXHAUSTED,
                    {
                        "attempts": quickfix_attempts,
                        "max_attempts": max_attempts,
                        "run_id": run_id,
                        "task_id": task.task_id,
                        "title": task.title,
                    },
                )
                if diagnostics_dir is None:
                    diagnostics_dir = plane._create_blocker_bundle(
                        run_id,
                        "Quickfix",
                        "Quickfix attempts exhausted (still QUICKFIX_NEEDED)",
                        stage_results[-1] if stage_results else None,
                    )
                node_id = "troubleshoot"
                continue
            plane._emit_event(
                EventType.QUICKFIX_ATTEMPT,
                {
                    "attempt": next_attempt,
                    "max_attempts": max_attempts,
                    "run_id": run_id,
                    "task_id": task.task_id,
                    "title": task.title,
                },
            )
            quickfix_attempts = next_attempt
        stage_type = stage_type_for_node(plane, node_id)
        result = plane._run_stage(stage_type, task, run_id, node_id=node_id)
        stage_results.append(result)
        if (
            result.runner_result is None
            and "policy_preflight" in result.metadata
            and result.status in {ExecutionStatus.BLOCKED.value, ExecutionStatus.NET_WAIT.value}
        ):
            blocked_status = ExecutionStatus(result.status)
            plane._record_stage_transition(
                result,
                task_before=task,
                task_after=task,
                routing_mode=routing_mode,
                selected_edge_id=(
                    "execution.policy_preflight.net_wait"
                    if blocked_status is ExecutionStatus.NET_WAIT
                    else "execution.policy_preflight.blocked"
                ),
                selected_edge_reason=(
                    f"{transition_reason_prefix}: {node_id} blocked by execution preflight ({blocked_status.value})"
                ),
                condition_inputs={
                    "status": blocked_status.value,
                    "policy_preflight": result.metadata.get("policy_preflight"),
                },
                condition_result=False,
                attributes={"policy_preflight_outcome": blocked_status.value.lower()},
            )
            return blocked_status, None, None, diagnostics_dir, quickfix_attempts
        transition, derived_outcome, condition_result, condition_inputs = select_transition(
            plane,
            node_id,
            trigger_status=result.status,
            facts=routing_facts(plane),
            artifacts=tuple(result.artifacts),
        )
        queue_mutations_applied: tuple[str, ...] = ()
        if transition.terminal_state_id == "idle":
            queue_mutations_applied = ("archive_task",)
        if transition.terminal_state_id == "needs_research":
            queue_mutations_applied = ("quarantine_task",)
        if diagnostics_dir is None and transition.to_node_id in {"troubleshoot", "consult"} and node_id not in {
            "troubleshoot",
            "consult",
        }:
            diagnostics_dir = plane._create_blocker_bundle(
                run_id,
                stage_type.value.title(),
                f"exit={result.exit_code} status={result.status}",
                result,
            )
        plane._record_stage_transition(
            result,
            task_before=task,
            task_after=(None if transition.terminal_state_id in {"idle", "needs_research"} else task),
            routing_mode=routing_mode,
            selected_edge_id=transition.edge_id,
            selected_edge_reason=(
                f"{transition_reason_prefix}: {node_id} -> "
                f"{transition.to_node_id or transition.terminal_state_id} on {derived_outcome}"
            ),
            selected_terminal_state_id=transition.terminal_state_id,
            condition_inputs=condition_inputs,
            condition_result=condition_result,
            queue_mutations_applied=queue_mutations_applied,
        )
        if transition.to_node_id is not None:
            if transition.to_node_id == "builder" and node_id in {"troubleshoot", "consult"}:
                quickfix_attempts = 0
            if node_id == "doublecheck" and derived_outcome == "quickfix_needed":
                if quickfix_attempts >= plane.config.execution.quickfix_max_attempts:
                    plane._emit_event(
                        EventType.QUICKFIX_EXHAUSTED,
                        {
                            "attempts": quickfix_attempts,
                            "max_attempts": plane.config.execution.quickfix_max_attempts,
                            "run_id": run_id,
                            "task_id": task.task_id,
                            "title": task.title,
                        },
                    )
                    if diagnostics_dir is None:
                        diagnostics_dir = plane._create_blocker_bundle(
                            run_id,
                            "Quickfix",
                            "Quickfix attempts exhausted (still QUICKFIX_NEEDED)",
                            result,
                        )
                    node_id = "troubleshoot"
                    continue
            node_id = transition.to_node_id
            continue
        return apply_terminal_transition(
            plane,
            transition,
            task=task,
            run_id=run_id,
            stage_results=stage_results,
            diagnostics_dir=diagnostics_dir,
            quickfix_attempts=quickfix_attempts,
        )


def resume_from_completed_status(
    plane: ExecutionRoutingPlane,
    task: TaskCard,
    *,
    run_id: str,
    stage_results: list[StageResult],
    status: ExecutionStatus,
    routing_mode_frozen_plan_legacy_resume: str,
) -> ExecutionOutcome:
    """Resume a frozen-plan run from one legacy completed execution status."""

    if status is ExecutionStatus.QUICKFIX_NEEDED:
        return run_frozen_plan(
            plane,
            task,
            run_id=run_id,
            stage_results=stage_results,
            start_node_id="hotfix",
            transition_reason_prefix="legacy quickfix resume fallback into frozen plan",
            routing_mode=routing_mode_frozen_plan_legacy_resume,
        )
    completed_node = legacy_resume_completed_node(plane, status)
    if completed_node is None:
        raise StageExecutionError(f"execution plane does not support resume from {status.value}")
    transition, _, _, _ = select_transition(
        plane,
        completed_node,
        trigger_status=status.value,
        facts=routing_facts(plane),
    )
    if transition.to_node_id is not None:
        return run_frozen_plan(
            plane,
            task,
            run_id=run_id,
            stage_results=stage_results,
            start_node_id=transition.to_node_id,
            transition_reason_prefix=f"legacy {status.value} resume mapped into frozen plan",
            routing_mode=routing_mode_frozen_plan_legacy_resume,
        )
    return apply_terminal_transition(
        plane,
        transition,
        task=task,
        run_id=run_id,
        stage_results=stage_results,
        quickfix_attempts=0,
    )


__all__ = [
    "apply_terminal_transition",
    "condition_matches",
    "derive_stage_outcome",
    "execution_plan",
    "legacy_resume_completed_node",
    "resume_from_completed_status",
    "routing_facts",
    "run_frozen_plan",
    "select_transition",
    "stage_plan",
    "stage_type_for_node",
]
