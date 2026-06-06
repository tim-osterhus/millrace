from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from millrace_ai.architecture import CompiledRunPlan, FrozenGraphPlanePlan
from millrace_ai.compiler import compile_and_persist_workspace_plan
from millrace_ai.config import RuntimeConfig
from millrace_ai.contracts import (
    ExecutionStageName,
    ExecutionTerminalResult,
    LearningStageName,
    Plane,
    PlanningStageName,
    PlanningTerminalResult,
    RecoveryCounterEntry,
    RecoveryCounters,
    ResultClass,
    RuntimeSnapshot,
    StageName,
    StageResultEnvelope,
    TerminalOutcome,
    WorkItemKind,
)
from millrace_ai.paths import bootstrap_workspace, workspace_paths
from millrace_ai.runtime.graph_authority import route_stage_result_from_graph

NOW = datetime(2026, 6, 1, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    (
        "mode_id",
        "plane",
        "stage",
        "source_node_id",
        "old_outcome",
        "custom_outcome",
        "terminal_state_id",
        "expected_reason",
    ),
    (
        (
            "standard_plain",
            Plane.EXECUTION,
            ExecutionStageName.UPDATER,
            "updater",
            ExecutionTerminalResult.UPDATE_COMPLETE.value,
            "CUSTOM_EXECUTION_DONE",
            "update_complete",
            "updater:CUSTOM_EXECUTION_DONE",
        ),
        (
            "standard_plain",
            Plane.PLANNING,
            PlanningStageName.MANAGER,
            "manager",
            PlanningTerminalResult.MANAGER_COMPLETE.value,
            "CUSTOM_PLANNING_DONE",
            "manager_complete",
            "manager:CUSTOM_PLANNING_DONE",
        ),
        (
            "learning_codex",
            Plane.LEARNING,
            LearningStageName.CURATOR,
            "curator",
            "CURATOR_COMPLETE",
            "CUSTOM_LEARNING_DONE",
            "learning_complete",
            "curator:CUSTOM_LEARNING_DONE",
        ),
    ),
)
def test_graph_authority_routes_custom_terminal_outcome_strings_through_terminal_actions(
    tmp_path: Path,
    mode_id: str,
    plane: Plane,
    stage: StageName,
    source_node_id: str,
    old_outcome: str,
    custom_outcome: str,
    terminal_state_id: str,
    expected_reason: str,
) -> None:
    plan = _compiled_plan(tmp_path, mode_id=mode_id)
    graph = plan.graphs_by_plane[plane]
    updated_graph = _replace_terminal_transition_outcome(
        graph,
        source_node_id=source_node_id,
        old_outcome=old_outcome,
        new_outcome=custom_outcome,
    )
    plan = _replace_graph(plan, plane, updated_graph)

    decision = route_stage_result_from_graph(
        plan,
        _snapshot(plane=plane, stage=stage, node_id=source_node_id),
        _stage_result(plane=plane, stage=stage, terminal_result=custom_outcome),
        RecoveryCounters(),
    )

    assert decision.action.value == "idle"
    assert decision.reason == expected_reason
    assert decision.terminal_state_id == terminal_state_id
    assert decision.terminal_action_id == "complete_work_item"
    assert decision.terminal_action_router_consequence == "idle"
    assert decision.lifecycle_mutation_plan_id == "complete_work_item"
    assert decision.lifecycle_action_id == "complete"


def test_threshold_exhaustion_uses_terminal_action_metadata(tmp_path: Path) -> None:
    plan = _compiled_plan(tmp_path, mode_id="standard_plain")
    snapshot = _snapshot(
        plane=Plane.PLANNING,
        stage=PlanningStageName.MECHANIC,
        node_id="mechanic",
        work_item_kind=WorkItemKind.SPEC,
        work_item_id="spec-001",
        current_failure_class="mechanic_blocked",
    )
    counters = RecoveryCounters(
        entries=(
            RecoveryCounterEntry(
                failure_class="mechanic_blocked",
                work_item_kind=WorkItemKind.SPEC,
                work_item_id="spec-001",
                mechanic_attempt_count=2,
                last_updated_at=NOW,
            ),
        )
    )

    decision = route_stage_result_from_graph(
        plan,
        snapshot,
        _stage_result(
            plane=Plane.PLANNING,
            stage=PlanningStageName.MECHANIC,
            terminal_result=PlanningTerminalResult.BLOCKED.value,
            work_item_kind=WorkItemKind.SPEC,
            work_item_id="spec-001",
            result_class=ResultClass.BLOCKED,
            metadata={"failure_class": "mechanic_blocked"},
        ),
        counters,
    )

    assert decision.action.value == "blocked"
    assert decision.reason == "mechanic_blocked:mechanic_attempts_exhausted"
    assert decision.failure_class == "mechanic_blocked"
    assert decision.terminal_state_id == "blocked"
    assert decision.terminal_action_id == "block_work_item"
    assert decision.terminal_action_router_consequence == "blocked"
    assert decision.lifecycle_mutation_plan_id == "block_work_item"
    assert decision.lifecycle_action_id == "block"


def test_execution_blocked_threshold_exhaustion_uses_exhausted_counter_mutation(
    tmp_path: Path,
) -> None:
    plan = _compiled_plan(tmp_path, mode_id="standard_plain")
    snapshot = _snapshot(
        plane=Plane.EXECUTION,
        stage=ExecutionStageName.TROUBLESHOOTER,
        node_id="troubleshooter",
        current_failure_class="builder_blocked",
    )
    counters = RecoveryCounters(
        entries=(
            RecoveryCounterEntry(
                failure_class="builder_blocked",
                work_item_kind=WorkItemKind.TASK,
                work_item_id="task-001",
                troubleshoot_attempt_count=2,
                last_updated_at=NOW,
            ),
        )
    )

    decision = route_stage_result_from_graph(
        plan,
        snapshot,
        _stage_result(
            plane=Plane.EXECUTION,
            stage=ExecutionStageName.TROUBLESHOOTER,
            terminal_result=ExecutionTerminalResult.BLOCKED.value,
            result_class=ResultClass.BLOCKED,
            metadata={"failure_class": "builder_blocked"},
        ),
        counters,
    )

    assert decision.action.value == "run_stage"
    assert decision.next_node_id == "consultant"
    assert decision.failure_class == "builder_blocked"
    assert decision.counter_mutation_name == "consultant_invocations"
    assert decision.recovery_counter_name == "consultant_invocations"


def test_recon_custom_terminal_outcome_carries_terminal_runtime_operation(
    tmp_path: Path,
) -> None:
    plan = _compiled_plan(tmp_path, mode_id="standard_plain")
    graph = plan.graphs_by_plane[Plane.PLANNING]
    updated_graph = _replace_terminal_transition_outcome(
        graph,
        source_node_id="recon",
        old_outcome=PlanningTerminalResult.RECON_TO_EXECUTION.value,
        new_outcome="CUSTOM_RECON_TASK",
    )
    plan = _replace_graph(plan, Plane.PLANNING, updated_graph)

    decision = route_stage_result_from_graph(
        plan,
        _snapshot(
            plane=Plane.PLANNING,
            stage=PlanningStageName.RECON,
            node_id="recon",
            work_item_kind=WorkItemKind.PROBE,
            work_item_id="probe-001",
        ),
        _stage_result(
            plane=Plane.PLANNING,
            stage=PlanningStageName.RECON,
            terminal_result="CUSTOM_RECON_TASK",
            work_item_kind=WorkItemKind.PROBE,
            work_item_id="probe-001",
        ),
        RecoveryCounters(),
    )

    assert decision.action.value == "idle"
    assert decision.terminal_state_id == "recon_to_execution"
    assert decision.terminal_action_id == "recon_enqueue_task"
    assert decision.runtime_operation_id == "recon.enqueue_task"


def _compiled_plan(tmp_path: Path, *, mode_id: str) -> CompiledRunPlan:
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    outcome = compile_and_persist_workspace_plan(
        paths.root,
        config=RuntimeConfig(),
        requested_mode_id=mode_id,
    )
    assert outcome.active_plan is not None
    return outcome.active_plan


def _replace_terminal_transition_outcome(
    graph: FrozenGraphPlanePlan,
    *,
    source_node_id: str,
    old_outcome: str,
    new_outcome: str,
) -> FrozenGraphPlanePlan:
    return graph.model_copy(
        update={
            "compiled_transitions": tuple(
                transition.model_copy(update={"outcome": new_outcome})
                if (
                    transition.source_node_id == source_node_id
                    and transition.outcome == old_outcome
                    and transition.terminal_state_id is not None
                )
                else transition
                for transition in graph.compiled_transitions
            )
        }
    )


def _replace_graph(
    plan: CompiledRunPlan,
    plane: Plane,
    graph: FrozenGraphPlanePlan,
) -> CompiledRunPlan:
    update: dict[str, object] = {
        "graphs_by_plane": {
            **plan.graphs_by_plane,
            plane: graph,
        }
    }
    if plane is Plane.EXECUTION:
        update["execution_graph"] = graph
    elif plane is Plane.PLANNING:
        update["planning_graph"] = graph
    else:
        update["learning_graph"] = graph
    return plan.model_copy(update=update)


def _snapshot(
    *,
    plane: Plane,
    stage: StageName,
    node_id: str,
    work_item_kind: WorkItemKind = WorkItemKind.TASK,
    work_item_id: str = "task-001",
    current_failure_class: str | None = None,
) -> RuntimeSnapshot:
    return RuntimeSnapshot(
        runtime_mode="daemon",
        process_running=True,
        paused=False,
        active_mode_id="default_codex",
        execution_loop_id="execution.standard",
        planning_loop_id="planning.standard",
        compiled_plan_id="plan-001",
        compiled_plan_path="state/compiled_plan.json",
        active_plane=plane,
        active_stage=stage,
        active_node_id=node_id,
        active_stage_kind_id=node_id,
        active_run_id="run-001",
        active_work_item_kind=work_item_kind,
        active_work_item_id=work_item_id,
        execution_status_marker="### IDLE",
        planning_status_marker="### IDLE",
        current_failure_class=current_failure_class,
        config_version="cfg-001",
        watcher_mode="off",
        updated_at=NOW,
    )


def _stage_result(
    *,
    plane: Plane,
    stage: StageName,
    terminal_result: str,
    work_item_kind: WorkItemKind = WorkItemKind.TASK,
    work_item_id: str = "task-001",
    result_class: ResultClass = ResultClass.SUCCESS,
    metadata: dict[str, object] | None = None,
) -> StageResultEnvelope:
    return StageResultEnvelope(
        run_id="run-001",
        plane=plane,
        stage=stage,
        node_id=stage.value,
        stage_kind_id=stage.value,
        work_item_kind=work_item_kind,
        work_item_id=work_item_id,
        terminal_result=TerminalOutcome(terminal_result),
        result_class=result_class,
        summary_status_marker=f"### {terminal_result}",
        success=result_class is ResultClass.SUCCESS,
        metadata=dict(metadata or {}),
        started_at=NOW,
        completed_at=NOW,
    )
