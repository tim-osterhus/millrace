from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from millrace_ai.architecture import FrozenGraphPlanePlan, FrozenGraphRunPlan
from millrace_ai.compiler import compile_and_persist_workspace_plan
from millrace_ai.config import RuntimeConfig
from millrace_ai.contracts import (
    ClosureTargetState,
    ExecutionStageName,
    ExecutionTerminalResult,
    Plane,
    PlanningStageName,
    PlanningTerminalResult,
    RecoveryCounters,
    ResultClass,
    RuntimeSnapshot,
    StageName,
    StageResultEnvelope,
    TerminalResult,
    WorkItemKind,
)
from millrace_ai.paths import bootstrap_workspace
from millrace_ai.router import RouterAction, next_execution_step, next_planning_step
from millrace_ai.runner import RunnerRawResult, StageRunRequest
from millrace_ai.runtime import RuntimeEngine
from millrace_ai.runtime.activation import entry_stage_for_kind
from millrace_ai.runtime.completion_behavior import maybe_activate_completion_stage
from millrace_ai.workspace.arbiter_state import save_closure_target_state
from millrace_ai.workspace.paths import WorkspacePaths

NOW = datetime(2026, 4, 21, tzinfo=timezone.utc)


@dataclass(frozen=True, slots=True)
class SimulatedDecision:
    action: RouterAction
    next_plane: Plane | None
    next_stage: StageName | None
    create_incident: bool = False


@dataclass(frozen=True, slots=True)
class RouterEquivalenceCase:
    name: str
    plane: Plane
    source_stage: StageName
    terminal_result: TerminalResult
    work_item_kind: WorkItemKind
    work_item_id: str
    metadata: dict[str, object] = field(default_factory=dict)
    fix_cycle_count: int = 0
    current_failure_class: str | None = None
    counter_values: dict[str, int] = field(default_factory=dict)
    counters: RecoveryCounters = field(default_factory=RecoveryCounters)
    closure_target: bool = False


def _result_class_for_terminal(terminal_result: TerminalResult) -> ResultClass:
    if terminal_result is ExecutionTerminalResult.FIX_NEEDED:
        return ResultClass.FOLLOWUP_NEEDED
    if terminal_result is ExecutionTerminalResult.NEEDS_PLANNING:
        return ResultClass.ESCALATE_PLANNING
    if terminal_result is PlanningTerminalResult.REMEDIATION_NEEDED:
        return ResultClass.FOLLOWUP_NEEDED
    if terminal_result in {ExecutionTerminalResult.BLOCKED, PlanningTerminalResult.BLOCKED}:
        return ResultClass.BLOCKED
    return ResultClass.SUCCESS


def _snapshot(
    *,
    plane: Plane,
    stage: StageName,
    work_item_kind: WorkItemKind | None,
    work_item_id: str | None,
    fix_cycle_count: int = 0,
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
        active_run_id="run-001",
        active_work_item_kind=work_item_kind,
        active_work_item_id=work_item_id,
        execution_status_marker="### IDLE",
        planning_status_marker="### IDLE",
        fix_cycle_count=fix_cycle_count,
        current_failure_class=current_failure_class,
        config_version="cfg-001",
        watcher_mode="off",
        updated_at=NOW,
    )


def _stage_result(
    *,
    stage: StageName,
    terminal_result: TerminalResult,
    work_item_kind: WorkItemKind,
    work_item_id: str,
    metadata: dict[str, object] | None = None,
    closure_target: bool = False,
) -> StageResultEnvelope:
    plane = Plane.EXECUTION if isinstance(stage, ExecutionStageName) else Plane.PLANNING
    payload = dict(metadata or {})
    if closure_target:
        payload.setdefault("request_kind", "closure_target")
        payload.setdefault("closure_target_root_spec_id", work_item_id)
        payload.setdefault("closure_target_root_idea_id", "idea-001")

    result_class = _result_class_for_terminal(terminal_result)
    return StageResultEnvelope(
        run_id="run-001",
        plane=plane,
        stage=stage,
        work_item_kind=work_item_kind,
        work_item_id=work_item_id,
        terminal_result=terminal_result,
        result_class=result_class,
        summary_status_marker=f"### {terminal_result.value}",
        success=result_class is ResultClass.SUCCESS,
        metadata=payload,
        started_at=NOW,
        completed_at=NOW,
    )


def _decision_shape(decision: SimulatedDecision) -> tuple[str, str | None, str | None, bool]:
    return (
        decision.action.value,
        decision.next_plane.value if decision.next_plane is not None else None,
        decision.next_stage.value if decision.next_stage is not None else None,
        decision.create_incident,
    )


def _router_decision_shape(decision: object) -> tuple[str, str | None, str | None, bool]:
    return (
        decision.action.value,
        decision.next_plane.value if decision.next_plane is not None else None,
        decision.next_stage.value if decision.next_stage is not None else None,
        bool(decision.create_incident),
    )


def _stage_for_node(plane: Plane, node_id: str) -> StageName:
    if plane is Plane.EXECUTION:
        return ExecutionStageName(node_id)
    return PlanningStageName(node_id)


def _simulate_graph_decision(
    graph: FrozenGraphPlanePlan,
    *,
    source_node_id: str,
    outcome: str,
    metadata: dict[str, object] | None = None,
    counter_values: dict[str, int] | None = None,
) -> SimulatedDecision:
    metadata = metadata or {}
    counter_values = counter_values or {}

    for policy in graph.compiled_resume_policies:
        if policy.source_node_id != source_node_id or policy.on_outcome != outcome:
            continue
        target_node_id = policy.default_target_node_id
        for key in policy.metadata_stage_keys:
            candidate = metadata.get(key)
            if not isinstance(candidate, str) or not candidate.strip():
                continue
            normalized = candidate.strip().lower()
            if normalized in policy.disallowed_target_node_ids:
                continue
            if normalized not in {node.node_id for node in graph.nodes}:
                continue
            target_node_id = normalized
            break
        return SimulatedDecision(
            action=RouterAction.RUN_STAGE,
            next_plane=graph.plane,
            next_stage=_stage_for_node(graph.plane, target_node_id),
        )

    for policy in graph.compiled_threshold_policies:
        if source_node_id not in policy.source_node_ids or policy.on_outcome != outcome:
            continue
        if counter_values.get(policy.counter_name.value, 0) < policy.threshold:
            break
        return _decision_from_target_or_terminal(
            graph,
            source_node_id=source_node_id,
            target_node_id=policy.exhausted_target_node_id,
            terminal_state_id=policy.exhausted_terminal_state_id,
        )

    for transition in graph.compiled_transitions:
        if transition.source_node_id == source_node_id and transition.outcome == outcome:
            return _decision_from_target_or_terminal(
                graph,
                source_node_id=source_node_id,
                target_node_id=transition.target_node_id,
                terminal_state_id=transition.terminal_state_id,
            )

    raise AssertionError(f"missing compiled graph transition for {graph.loop_id}:{source_node_id}:{outcome}")


def _decision_from_target_or_terminal(
    graph: FrozenGraphPlanePlan,
    *,
    source_node_id: str,
    target_node_id: str | None,
    terminal_state_id: str | None,
) -> SimulatedDecision:
    if target_node_id is not None:
        return SimulatedDecision(
            action=RouterAction.RUN_STAGE,
            next_plane=graph.plane,
            next_stage=_stage_for_node(graph.plane, target_node_id),
        )

    assert terminal_state_id is not None
    terminal_state = next(
        state for state in graph.terminal_states if state.terminal_state_id == terminal_state_id
    )
    if terminal_state.terminal_class.value == "success":
        return SimulatedDecision(
            action=RouterAction.IDLE,
            next_plane=None,
            next_stage=None,
        )
    if terminal_state.terminal_class.value == "blocked":
        return SimulatedDecision(
            action=RouterAction.BLOCKED,
            next_plane=None,
            next_stage=None,
        )
    if terminal_state.terminal_class.value in {"escalate_planning", "followup_needed"}:
        create_incident = terminal_state.terminal_class.value == "escalate_planning"
        if (
            terminal_state.terminal_class.value == "followup_needed"
            and graph.compiled_completion_entry is not None
            and source_node_id == graph.compiled_completion_entry.node_id
            and terminal_state_id == graph.compiled_completion_entry.on_gap_terminal_state_id
        ):
            create_incident = graph.compiled_completion_entry.create_incident_on_gap
        return SimulatedDecision(
            action=RouterAction.HANDOFF,
            next_plane=Plane.PLANNING,
            next_stage=PlanningStageName.AUDITOR,
            create_incident=create_incident,
        )
    raise AssertionError(
        f"unsupported terminal class for {graph.loop_id}:{source_node_id}:{terminal_state.terminal_class.value}"
    )


def _workspace(tmp_path: Path) -> WorkspacePaths:
    return bootstrap_workspace(tmp_path / "workspace")


def _load_compiled_graph_plan(paths: WorkspacePaths) -> FrozenGraphRunPlan:
    outcome = compile_and_persist_workspace_plan(
        paths,
        config=RuntimeConfig(),
        requested_mode_id="default_codex",
    )
    assert outcome.diagnostics.ok is True
    return FrozenGraphRunPlan.model_validate_json(
        (paths.state_dir / "compiled_graph_plan.json").read_text(encoding="utf-8")
    )


def _unused_stage_runner(request: StageRunRequest) -> RunnerRawResult:
    raise AssertionError(f"stage runner should not be called in equivalence setup: {request.stage.value}")


def _closure_target_state() -> ClosureTargetState:
    return ClosureTargetState(
        root_spec_id="spec-root-001",
        root_idea_id="idea-001",
        root_spec_path="millrace-agents/arbiter/contracts/root-specs/spec-root-001.md",
        root_idea_path="millrace-agents/arbiter/contracts/ideas/idea-001.md",
        rubric_path="millrace-agents/arbiter/rubrics/spec-root-001.md",
        latest_verdict_path=None,
        latest_report_path=None,
        closure_open=True,
        closure_blocked_by_lineage_work=False,
        blocking_work_ids=(),
        opened_at=NOW,
    )


def test_compiled_graph_intake_activation_matches_legacy_activation_surfaces(tmp_path: Path) -> None:
    workspace_root = _workspace(tmp_path)
    graph_plan = _load_compiled_graph_plan(workspace_root)

    execution_entries = {
        entry.entry_key.value: entry.node_id for entry in graph_plan.execution_graph.compiled_entries
    }
    planning_entries = {
        entry.entry_key.value: entry.node_id for entry in graph_plan.planning_graph.compiled_entries
    }

    assert execution_entries["task"] == entry_stage_for_kind(WorkItemKind.TASK).value
    assert planning_entries["spec"] == entry_stage_for_kind(WorkItemKind.SPEC).value
    assert planning_entries["incident"] == entry_stage_for_kind(WorkItemKind.INCIDENT).value

    save_closure_target_state(workspace_root, _closure_target_state())
    engine = RuntimeEngine(workspace_root, stage_runner=_unused_stage_runner)
    engine.startup()

    activated = maybe_activate_completion_stage(engine)

    assert activated is not None
    assert graph_plan.planning_graph.compiled_completion_entry is not None
    assert engine.snapshot is not None
    assert engine.snapshot.active_stage is not None
    assert (
        engine.snapshot.active_stage.value
        == graph_plan.planning_graph.compiled_completion_entry.node_id
    )


def test_shipped_compiled_graph_semantics_match_legacy_router_cases(tmp_path: Path) -> None:
    workspace_root = _workspace(tmp_path)
    graph_plan = _load_compiled_graph_plan(workspace_root)

    execution_cases = (
        RouterEquivalenceCase(
            name="execution:builder:BUILDER_COMPLETE",
            plane=Plane.EXECUTION,
            source_stage=ExecutionStageName.BUILDER,
            terminal_result=ExecutionTerminalResult.BUILDER_COMPLETE,
            work_item_kind=WorkItemKind.TASK,
            work_item_id="task-001",
        ),
        RouterEquivalenceCase(
            name="execution:checker:CHECKER_PASS",
            plane=Plane.EXECUTION,
            source_stage=ExecutionStageName.CHECKER,
            terminal_result=ExecutionTerminalResult.CHECKER_PASS,
            work_item_kind=WorkItemKind.TASK,
            work_item_id="task-001",
        ),
        RouterEquivalenceCase(
            name="execution:fixer:FIXER_COMPLETE",
            plane=Plane.EXECUTION,
            source_stage=ExecutionStageName.FIXER,
            terminal_result=ExecutionTerminalResult.FIXER_COMPLETE,
            work_item_kind=WorkItemKind.TASK,
            work_item_id="task-001",
        ),
        RouterEquivalenceCase(
            name="execution:doublechecker:DOUBLECHECK_PASS",
            plane=Plane.EXECUTION,
            source_stage=ExecutionStageName.DOUBLECHECKER,
            terminal_result=ExecutionTerminalResult.DOUBLECHECK_PASS,
            work_item_kind=WorkItemKind.TASK,
            work_item_id="task-001",
        ),
        RouterEquivalenceCase(
            name="execution:updater:UPDATE_COMPLETE",
            plane=Plane.EXECUTION,
            source_stage=ExecutionStageName.UPDATER,
            terminal_result=ExecutionTerminalResult.UPDATE_COMPLETE,
            work_item_kind=WorkItemKind.TASK,
            work_item_id="task-001",
        ),
        RouterEquivalenceCase(
            name="execution:checker:FIX_NEEDED:under-threshold",
            plane=Plane.EXECUTION,
            source_stage=ExecutionStageName.CHECKER,
            terminal_result=ExecutionTerminalResult.FIX_NEEDED,
            work_item_kind=WorkItemKind.TASK,
            work_item_id="task-001",
        ),
        RouterEquivalenceCase(
            name="execution:doublechecker:FIX_NEEDED:exhausted",
            plane=Plane.EXECUTION,
            source_stage=ExecutionStageName.DOUBLECHECKER,
            terminal_result=ExecutionTerminalResult.FIX_NEEDED,
            work_item_kind=WorkItemKind.TASK,
            work_item_id="task-001",
            fix_cycle_count=2,
            counter_values={"fix_cycle_count": 2},
        ),
        RouterEquivalenceCase(
            name="execution:builder:BLOCKED:under-threshold",
            plane=Plane.EXECUTION,
            source_stage=ExecutionStageName.BUILDER,
            terminal_result=ExecutionTerminalResult.BLOCKED,
            work_item_kind=WorkItemKind.TASK,
            work_item_id="task-001",
            metadata={"failure_class": "builder_blocked"},
            counters=RecoveryCounters(
                entries=(
                    {
                        "failure_class": "builder_blocked",
                        "work_item_kind": WorkItemKind.TASK,
                        "work_item_id": "task-001",
                        "troubleshoot_attempt_count": 0,
                        "last_updated_at": NOW,
                    },
                )
            ),
            counter_values={"troubleshoot_attempt_count": 0},
        ),
        RouterEquivalenceCase(
            name="execution:updater:BLOCKED:exhausted",
            plane=Plane.EXECUTION,
            source_stage=ExecutionStageName.UPDATER,
            terminal_result=ExecutionTerminalResult.BLOCKED,
            work_item_kind=WorkItemKind.TASK,
            work_item_id="task-001",
            metadata={"failure_class": "updater_blocked"},
            counters=RecoveryCounters(
                entries=(
                    {
                        "failure_class": "updater_blocked",
                        "work_item_kind": WorkItemKind.TASK,
                        "work_item_id": "task-001",
                        "troubleshoot_attempt_count": 2,
                        "last_updated_at": NOW,
                    },
                )
            ),
            counter_values={"troubleshoot_attempt_count": 2},
        ),
        RouterEquivalenceCase(
            name="execution:troubleshooter:TROUBLESHOOT_COMPLETE:default",
            plane=Plane.EXECUTION,
            source_stage=ExecutionStageName.TROUBLESHOOTER,
            terminal_result=ExecutionTerminalResult.TROUBLESHOOT_COMPLETE,
            work_item_kind=WorkItemKind.TASK,
            work_item_id="task-001",
        ),
        RouterEquivalenceCase(
            name="execution:troubleshooter:TROUBLESHOOT_COMPLETE:resume-checker",
            plane=Plane.EXECUTION,
            source_stage=ExecutionStageName.TROUBLESHOOTER,
            terminal_result=ExecutionTerminalResult.TROUBLESHOOT_COMPLETE,
            work_item_kind=WorkItemKind.TASK,
            work_item_id="task-001",
            metadata={"resume_stage": "checker"},
        ),
        RouterEquivalenceCase(
            name="execution:consultant:CONSULT_COMPLETE:default",
            plane=Plane.EXECUTION,
            source_stage=ExecutionStageName.CONSULTANT,
            terminal_result=ExecutionTerminalResult.CONSULT_COMPLETE,
            work_item_kind=WorkItemKind.TASK,
            work_item_id="task-001",
        ),
        RouterEquivalenceCase(
            name="execution:consultant:CONSULT_COMPLETE:target-updater",
            plane=Plane.EXECUTION,
            source_stage=ExecutionStageName.CONSULTANT,
            terminal_result=ExecutionTerminalResult.CONSULT_COMPLETE,
            work_item_kind=WorkItemKind.TASK,
            work_item_id="task-001",
            metadata={"target_stage": "updater"},
        ),
        RouterEquivalenceCase(
            name="execution:consultant:NEEDS_PLANNING",
            plane=Plane.EXECUTION,
            source_stage=ExecutionStageName.CONSULTANT,
            terminal_result=ExecutionTerminalResult.NEEDS_PLANNING,
            work_item_kind=WorkItemKind.TASK,
            work_item_id="task-001",
        ),
        RouterEquivalenceCase(
            name="execution:consultant:BLOCKED",
            plane=Plane.EXECUTION,
            source_stage=ExecutionStageName.CONSULTANT,
            terminal_result=ExecutionTerminalResult.BLOCKED,
            work_item_kind=WorkItemKind.TASK,
            work_item_id="task-001",
        ),
    )
    planning_cases = (
        RouterEquivalenceCase(
            name="planning:planner:PLANNER_COMPLETE",
            plane=Plane.PLANNING,
            source_stage=PlanningStageName.PLANNER,
            terminal_result=PlanningTerminalResult.PLANNER_COMPLETE,
            work_item_kind=WorkItemKind.SPEC,
            work_item_id="spec-001",
        ),
        RouterEquivalenceCase(
            name="planning:auditor:AUDITOR_COMPLETE",
            plane=Plane.PLANNING,
            source_stage=PlanningStageName.AUDITOR,
            terminal_result=PlanningTerminalResult.AUDITOR_COMPLETE,
            work_item_kind=WorkItemKind.INCIDENT,
            work_item_id="inc-001",
        ),
        RouterEquivalenceCase(
            name="planning:manager:MANAGER_COMPLETE",
            plane=Plane.PLANNING,
            source_stage=PlanningStageName.MANAGER,
            terminal_result=PlanningTerminalResult.MANAGER_COMPLETE,
            work_item_kind=WorkItemKind.SPEC,
            work_item_id="spec-001",
        ),
        RouterEquivalenceCase(
            name="planning:planner:BLOCKED:under-threshold",
            plane=Plane.PLANNING,
            source_stage=PlanningStageName.PLANNER,
            terminal_result=PlanningTerminalResult.BLOCKED,
            work_item_kind=WorkItemKind.SPEC,
            work_item_id="spec-001",
            metadata={"failure_class": "planning_artifact_mismatch"},
            counters=RecoveryCounters(
                entries=(
                    {
                        "failure_class": "planning_artifact_mismatch",
                        "work_item_kind": WorkItemKind.SPEC,
                        "work_item_id": "spec-001",
                        "mechanic_attempt_count": 0,
                        "last_updated_at": NOW,
                    },
                )
            ),
            counter_values={"mechanic_attempt_count": 0},
        ),
        RouterEquivalenceCase(
            name="planning:mechanic:BLOCKED:exhausted",
            plane=Plane.PLANNING,
            source_stage=PlanningStageName.MECHANIC,
            terminal_result=PlanningTerminalResult.BLOCKED,
            work_item_kind=WorkItemKind.SPEC,
            work_item_id="spec-001",
            metadata={"failure_class": "planning_artifact_mismatch"},
            current_failure_class="planning_artifact_mismatch",
            counters=RecoveryCounters(
                entries=(
                    {
                        "failure_class": "planning_artifact_mismatch",
                        "work_item_kind": WorkItemKind.SPEC,
                        "work_item_id": "spec-001",
                        "mechanic_attempt_count": 2,
                        "last_updated_at": NOW,
                    },
                )
            ),
            counter_values={"mechanic_attempt_count": 2},
        ),
        RouterEquivalenceCase(
            name="planning:mechanic:MECHANIC_COMPLETE:default",
            plane=Plane.PLANNING,
            source_stage=PlanningStageName.MECHANIC,
            terminal_result=PlanningTerminalResult.MECHANIC_COMPLETE,
            work_item_kind=WorkItemKind.SPEC,
            work_item_id="spec-001",
        ),
        RouterEquivalenceCase(
            name="planning:mechanic:MECHANIC_COMPLETE:resume-auditor",
            plane=Plane.PLANNING,
            source_stage=PlanningStageName.MECHANIC,
            terminal_result=PlanningTerminalResult.MECHANIC_COMPLETE,
            work_item_kind=WorkItemKind.SPEC,
            work_item_id="spec-001",
            metadata={"resume_stage": "auditor"},
        ),
        RouterEquivalenceCase(
            name="planning:arbiter:ARBITER_COMPLETE",
            plane=Plane.PLANNING,
            source_stage=PlanningStageName.ARBITER,
            terminal_result=PlanningTerminalResult.ARBITER_COMPLETE,
            work_item_kind=WorkItemKind.SPEC,
            work_item_id="spec-root-001",
            closure_target=True,
        ),
        RouterEquivalenceCase(
            name="planning:arbiter:REMEDIATION_NEEDED",
            plane=Plane.PLANNING,
            source_stage=PlanningStageName.ARBITER,
            terminal_result=PlanningTerminalResult.REMEDIATION_NEEDED,
            work_item_kind=WorkItemKind.SPEC,
            work_item_id="spec-root-001",
            closure_target=True,
        ),
        RouterEquivalenceCase(
            name="planning:arbiter:BLOCKED",
            plane=Plane.PLANNING,
            source_stage=PlanningStageName.ARBITER,
            terminal_result=PlanningTerminalResult.BLOCKED,
            work_item_kind=WorkItemKind.SPEC,
            work_item_id="spec-root-001",
            closure_target=True,
        ),
    )

    for case in (*execution_cases, *planning_cases):
        snapshot = _snapshot(
            plane=case.plane,
            stage=case.source_stage,
            work_item_kind=None if case.closure_target else case.work_item_kind,
            work_item_id=None if case.closure_target else case.work_item_id,
            fix_cycle_count=case.fix_cycle_count,
            current_failure_class=case.current_failure_class,
        )
        stage_result = _stage_result(
            stage=case.source_stage,
            terminal_result=case.terminal_result,
            work_item_kind=case.work_item_kind,
            work_item_id=case.work_item_id,
            metadata=case.metadata,
            closure_target=case.closure_target,
        )

        legacy_decision = (
            next_execution_step(snapshot, stage_result, case.counters)
            if case.plane is Plane.EXECUTION
            else next_planning_step(snapshot, stage_result, case.counters)
        )
        graph = (
            graph_plan.execution_graph
            if case.plane is Plane.EXECUTION
            else graph_plan.planning_graph
        )
        simulated_decision = _simulate_graph_decision(
            graph,
            source_node_id=case.source_stage.value,
            outcome=case.terminal_result.value,
            metadata=case.metadata,
            counter_values=case.counter_values,
        )

        assert _decision_shape(simulated_decision) == _router_decision_shape(
            legacy_decision
        ), case.name
