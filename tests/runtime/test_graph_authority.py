from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from millrace_ai.architecture import (
    CompiledGraphEntryPlan,
    CompiledRunPlan,
    GraphLoopEntryDefinition,
    WorkItemFamilyDefinition,
)
from millrace_ai.compiler import compile_and_persist_workspace_plan
from millrace_ai.config import RuntimeConfig
from millrace_ai.contracts import (
    ExecutionStageName,
    ExecutionTerminalResult,
    LearningStageName,
    LearningTerminalResult,
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
from millrace_ai.paths import bootstrap_workspace, workspace_paths
from millrace_ai.runner import RunnerRawResult, StageRunRequest
from millrace_ai.runtime import RuntimeEngine
from millrace_ai.runtime.graph_authority import (
    completion_activation_for_graph,
    route_stage_result_from_graph,
    work_item_activation_for_graph,
)
from millrace_ai.runtime.graph_authority.counters import counter_attempts, counter_key_from_snapshot

NOW = datetime(2026, 4, 23, tzinfo=timezone.utc)


def test_graph_authority_public_exports_remain_importable() -> None:
    import millrace_ai.runtime.graph_authority as graph_authority

    for name in graph_authority.__all__:
        assert hasattr(graph_authority, name), name


def test_custom_family_entry_key_can_activate_from_compiled_graph(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    outcome = compile_and_persist_workspace_plan(
        paths.root,
        config=RuntimeConfig(),
        requested_mode_id="standard_plain",
    )
    assert outcome.active_plan is not None
    family = _custom_planning_family()
    planning_graph = outcome.active_plan.planning_graph
    updated_planning_graph = planning_graph.model_copy(
        update={
            "entry_nodes": (
                *planning_graph.entry_nodes,
                GraphLoopEntryDefinition(entry_key="custom_review", node_id="planner"),
            ),
            "compiled_entries": (
                *planning_graph.compiled_entries,
                CompiledGraphEntryPlan(
                    entry_key="custom_review",
                    node_id="planner",
                    stage_kind_id="planner",
                    plane=Plane.PLANNING,
                ),
            ),
        }
    )
    plan = outcome.active_plan.model_copy(
        update={
            "work_item_families_by_id": {
                **outcome.active_plan.work_item_families_by_id,
                family.family_id: family,
            },
            "planning_graph": updated_planning_graph,
            "graphs_by_plane": {
                **outcome.active_plan.graphs_by_plane,
                Plane.PLANNING: updated_planning_graph,
            },
        }
    )

    activation = work_item_activation_for_graph(plan, "custom_review")

    assert activation.entry_key == "custom_review"
    assert activation.node_id == "planner"
    assert activation.stage is PlanningStageName.PLANNER


def test_custom_noncanonical_stage_kind_routes_with_compiled_runtime_stage(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    outcome = compile_and_persist_workspace_plan(
        paths.root,
        config=RuntimeConfig(),
        requested_mode_id="standard_plain",
    )
    assert outcome.active_plan is not None

    custom_stage_kind_id = "builder_custom"
    execution_graph = outcome.active_plan.execution_graph
    updated_execution_graph = execution_graph.model_copy(
        update={
            "nodes": tuple(
                node.model_copy(
                    update={
                        "stage_kind_id": custom_stage_kind_id,
                        "runtime_stage": ExecutionStageName.BUILDER,
                    }
                )
                if node.node_id == "builder"
                else node
                for node in execution_graph.nodes
            ),
            "compiled_entries": tuple(
                entry.model_copy(update={"stage_kind_id": custom_stage_kind_id})
                if entry.node_id == "builder"
                else entry
                for entry in execution_graph.compiled_entries
            ),
        }
    )
    plan = outcome.active_plan.model_copy(
        update={
            "execution_graph": updated_execution_graph,
            "graphs_by_plane": {
                **outcome.active_plan.graphs_by_plane,
                Plane.EXECUTION: updated_execution_graph,
            },
        }
    )

    activation = work_item_activation_for_graph(plan, WorkItemKind.TASK)
    assert activation.stage is ExecutionStageName.BUILDER
    assert activation.stage_kind_id == custom_stage_kind_id

    snapshot = _snapshot(
        plane=Plane.EXECUTION,
        stage=ExecutionStageName.BUILDER,
    ).model_copy(update={"active_stage_kind_id": custom_stage_kind_id})
    stage_result = _stage_result(
        stage=ExecutionStageName.BUILDER,
        terminal_result=ExecutionTerminalResult.BUILDER_COMPLETE,
    ).model_copy(update={"stage_kind_id": custom_stage_kind_id})

    decision = route_stage_result_from_graph(
        plan,
        snapshot,
        stage_result,
        RecoveryCounters(),
    )

    assert decision.action.value == "run_stage"
    assert decision.next_stage is ExecutionStageName.CHECKER
    assert decision.reason == "builder:BUILDER_COMPLETE"


def test_runtime_stage_compatibility_rejects_missing_canonical_node_stage(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    outcome = compile_and_persist_workspace_plan(
        paths.root,
        config=RuntimeConfig(),
        requested_mode_id="standard_plain",
    )
    assert outcome.active_plan is not None

    payload = outcome.active_plan.model_dump(mode="json")
    _remove_runtime_stage(payload["execution_graph"]["nodes"], node_id="builder")
    _remove_runtime_stage(payload["graphs_by_plane"]["execution"]["nodes"], node_id="builder")

    with pytest.raises(ValueError, match="runtime_stage"):
        CompiledRunPlan.model_validate(payload)


def test_runtime_stage_compatibility_rejects_missing_noncanonical_node_stage(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    outcome = compile_and_persist_workspace_plan(
        paths.root,
        config=RuntimeConfig(),
        requested_mode_id="standard_plain",
    )
    assert outcome.active_plan is not None

    payload = outcome.active_plan.model_dump(mode="json")
    _update_node(payload["execution_graph"]["nodes"], node_id="builder", stage_kind_id="builder_custom")
    _update_node(
        payload["graphs_by_plane"]["execution"]["nodes"],
        node_id="builder",
        stage_kind_id="builder_custom",
    )
    _remove_runtime_stage(payload["execution_graph"]["nodes"], node_id="builder")
    _remove_runtime_stage(payload["graphs_by_plane"]["execution"]["nodes"], node_id="builder")

    with pytest.raises(
        ValueError,
        match="runtime_stage",
    ):
        CompiledRunPlan.model_validate(payload)


def _workspace(tmp_path: Path):
    return bootstrap_workspace(workspace_paths(tmp_path / "workspace"))


def _remove_runtime_stage(nodes: list[dict[str, object]], *, node_id: str) -> None:
    for node in nodes:
        if node.get("node_id") == node_id:
            node.pop("runtime_stage", None)
            return
    raise AssertionError(f"missing node payload for {node_id}")


def _update_node(
    nodes: list[dict[str, object]],
    *,
    node_id: str,
    stage_kind_id: str,
) -> None:
    for node in nodes:
        if node.get("node_id") == node_id:
            node["stage_kind_id"] = stage_kind_id
            return
    raise AssertionError(f"missing node payload for {node_id}")


def _custom_planning_family() -> WorkItemFamilyDefinition:
    return WorkItemFamilyDefinition(
        family_id="custom_review",
        plane=Plane.PLANNING,
        entry_key="custom_review",
        display_name="Custom Review",
        document_kind="custom_review",
        runtime_relative_dir="custom/reviews",
        file_extension=".json",
        schema_id="custom_review_document_v1",
        document_adapter_id="custom_review_json_v1",
        queue_dirs={
            "queue": "custom/reviews/queue",
            "active": "custom/reviews/active",
            "done": "custom/reviews/done",
            "blocked": "custom/reviews/blocked",
            "canceled": "custom/reviews/canceled",
        },
        lifecycle_states=("queue", "active", "done", "blocked", "canceled"),
        claimable_state="queue",
        active_state="active",
        done_state="done",
        blocked_state="blocked",
        canceled_state="canceled",
        closure_blocking_states=("queue", "active", "blocked"),
        default_entry_key="custom_review",
        id_field="custom_id",
        created_at_field="created_at",
        lineage_fields=("root_spec_id",),
        operator_capabilities=("cancel", "inspect"),
    )


def _unused_stage_runner(request: StageRunRequest) -> RunnerRawResult:
    raise AssertionError(f"stage runner should not be called in graph-authority tests: {request.stage.value}")


def _snapshot(
    *,
    plane: Plane,
    stage: StageName,
    work_item_family_id: str | None = None,
    work_item_kind: WorkItemKind | None = WorkItemKind.TASK,
    work_item_id: str | None = "task-001",
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
        active_work_item_family_id=work_item_family_id,
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


def _stage_result(
    *,
    stage: StageName,
    terminal_result: TerminalResult,
    work_item_kind: WorkItemKind = WorkItemKind.TASK,
    work_item_id: str = "task-001",
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


def test_runtime_startup_loads_compiled_plan(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    engine = RuntimeEngine(paths, stage_runner=_unused_stage_runner)

    engine.startup()

    assert engine.compiled_plan is not None
    assert engine.compiled_plan.execution_graph.loop_id == "execution.standard"
    assert engine.compiled_plan.planning_graph.loop_id == "planning.standard"


def test_work_item_activation_resolves_from_compiled_plan_entries(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    engine = RuntimeEngine(paths, stage_runner=_unused_stage_runner)
    engine.startup()

    assert engine.compiled_plan is not None

    task = work_item_activation_for_graph(engine.compiled_plan, WorkItemKind.TASK)
    probe = work_item_activation_for_graph(engine.compiled_plan, WorkItemKind.PROBE)
    spec = work_item_activation_for_graph(engine.compiled_plan, WorkItemKind.SPEC)
    incident = work_item_activation_for_graph(engine.compiled_plan, WorkItemKind.INCIDENT)
    completion = completion_activation_for_graph(engine.compiled_plan)

    assert task.plane is Plane.EXECUTION
    assert task.stage is ExecutionStageName.BUILDER
    assert probe.plane is Plane.PLANNING
    assert probe.stage is PlanningStageName.RECON
    assert spec.plane is Plane.PLANNING
    assert spec.stage is PlanningStageName.PLANNER
    assert incident.plane is Plane.PLANNING
    assert incident.stage is PlanningStageName.AUDITOR
    assert completion.plane is Plane.PLANNING
    assert completion.stage is PlanningStageName.ARBITER


def test_work_item_activation_fails_when_required_entry_is_missing(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    engine = RuntimeEngine(paths, stage_runner=_unused_stage_runner)
    engine.startup()

    assert engine.compiled_plan is not None
    broken_graph_plan = engine.compiled_plan.model_copy(
        update={
            "execution_graph": engine.compiled_plan.execution_graph.model_copy(
                update={"compiled_entries": ()}
            )
        }
    )

    with pytest.raises(ValueError, match="task"):
        work_item_activation_for_graph(broken_graph_plan, WorkItemKind.TASK)


def test_completion_activation_fails_when_completion_entry_is_missing(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    engine = RuntimeEngine(paths, stage_runner=_unused_stage_runner)
    engine.startup()

    assert engine.compiled_plan is not None
    broken_graph_plan = engine.compiled_plan.model_copy(
        update={
            "planning_graph": engine.compiled_plan.planning_graph.model_copy(
                update={"compiled_completion_entry": None}
            )
        }
    )

    with pytest.raises(ValueError, match="closure_target"):
        completion_activation_for_graph(broken_graph_plan)


@pytest.mark.parametrize(
    ("snapshot", "stage_result", "counters", "expected_action", "expected_stage"),
    (
        (
            _snapshot(plane=Plane.EXECUTION, stage=ExecutionStageName.BUILDER),
            _stage_result(
                stage=ExecutionStageName.BUILDER,
                terminal_result=ExecutionTerminalResult.BUILDER_COMPLETE,
            ),
            RecoveryCounters(),
            "run_stage",
            ExecutionStageName.CHECKER,
        ),
        (
            _snapshot(
                plane=Plane.EXECUTION,
                stage=ExecutionStageName.DOUBLECHECKER,
                fix_cycle_count=2,
            ),
            _stage_result(
                stage=ExecutionStageName.DOUBLECHECKER,
                terminal_result=ExecutionTerminalResult.FIX_NEEDED,
            ),
            RecoveryCounters(),
            "run_stage",
            ExecutionStageName.TROUBLESHOOTER,
        ),
        (
            _snapshot(
                plane=Plane.EXECUTION,
                stage=ExecutionStageName.UPDATER,
                current_failure_class="updater_blocked",
            ),
            _stage_result(
                stage=ExecutionStageName.UPDATER,
                terminal_result=ExecutionTerminalResult.BLOCKED,
                metadata={"failure_class": "updater_blocked"},
            ),
            RecoveryCounters(
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
            "run_stage",
            ExecutionStageName.CONSULTANT,
        ),
        (
            _snapshot(
                plane=Plane.EXECUTION,
                stage=ExecutionStageName.TROUBLESHOOTER,
            ),
            _stage_result(
                stage=ExecutionStageName.TROUBLESHOOTER,
                terminal_result=ExecutionTerminalResult.TROUBLESHOOT_COMPLETE,
                metadata={"resume_stage": "checker"},
            ),
            RecoveryCounters(),
            "run_stage",
            ExecutionStageName.CHECKER,
        ),
        (
            _snapshot(
                plane=Plane.PLANNING,
                stage=PlanningStageName.RECON,
                work_item_kind=WorkItemKind.PROBE,
                work_item_id="probe-001",
            ),
            _stage_result(
                stage=PlanningStageName.RECON,
                terminal_result=PlanningTerminalResult.RECON_TO_EXECUTION,
                work_item_kind=WorkItemKind.PROBE,
                work_item_id="probe-001",
            ),
            RecoveryCounters(),
            "idle",
            None,
        ),
        (
            _snapshot(
                plane=Plane.PLANNING,
                stage=PlanningStageName.MECHANIC,
                work_item_kind=WorkItemKind.SPEC,
                work_item_id="spec-001",
                current_failure_class="planning_artifact_mismatch",
            ),
            _stage_result(
                stage=PlanningStageName.MECHANIC,
                terminal_result=PlanningTerminalResult.BLOCKED,
                work_item_kind=WorkItemKind.SPEC,
                work_item_id="spec-001",
                metadata={"failure_class": "planning_artifact_mismatch"},
            ),
            RecoveryCounters(
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
            "blocked",
            None,
        ),
        (
            _snapshot(
                plane=Plane.PLANNING,
                stage=PlanningStageName.ARBITER,
                work_item_kind=None,
                work_item_id=None,
            ),
            _stage_result(
                stage=PlanningStageName.ARBITER,
                terminal_result=PlanningTerminalResult.REMEDIATION_NEEDED,
                work_item_kind=WorkItemKind.SPEC,
                work_item_id="spec-root-001",
                closure_target=True,
            ),
            RecoveryCounters(),
            "handoff",
            PlanningStageName.AUDITOR,
        ),
    ),
)
def test_route_stage_result_from_graph_matches_shipped_default_semantics(
    tmp_path: Path,
    snapshot: RuntimeSnapshot,
    stage_result: StageResultEnvelope,
    counters: RecoveryCounters,
    expected_action: str,
    expected_stage: StageName | None,
) -> None:
    paths = _workspace(tmp_path)
    engine = RuntimeEngine(paths, stage_runner=_unused_stage_runner)
    engine.startup()

    assert engine.compiled_plan is not None

    decision = route_stage_result_from_graph(
        engine.compiled_plan,
        snapshot,
        stage_result,
        counters,
    )

    assert decision.action.value == expected_action
    assert decision.next_stage == expected_stage


def test_graph_authority_counters_use_family_id_without_legacy_kind() -> None:
    snapshot = _snapshot(
        plane=Plane.PLANNING,
        stage=PlanningStageName.PLANNER,
        work_item_family_id="custom_review",
        work_item_kind=None,
        work_item_id="custom-001",
        current_failure_class="custom_failure",
    )
    counters = RecoveryCounters(
        entries=(
            {
                "failure_class": "custom_failure",
                "work_item_family_id": "custom_review",
                "work_item_id": "custom-001",
                "mechanic_attempt_count": 2,
                "last_updated_at": NOW,
            },
        )
    )

    assert counter_key_from_snapshot(snapshot, "Custom Failure") == (
        "custom_review:custom-001:custom_failure"
    )
    assert counter_attempts(snapshot, counters, "custom_failure", plane=Plane.PLANNING) == 2


@pytest.mark.parametrize(
    ("terminal_result", "expected_stage", "expected_reason"),
    (
        (
            ExecutionTerminalResult.INTEGRATION_COMPLETE,
            ExecutionStageName.CHECKER,
            "integrator:INTEGRATION_COMPLETE",
        ),
        (
            ExecutionTerminalResult.BLOCKED,
            ExecutionStageName.TROUBLESHOOTER,
            "integrator_blocked",
        ),
    ),
)
def test_integrated_graph_routes_integrator_to_checker_or_recovery(
    tmp_path: Path,
    terminal_result: ExecutionTerminalResult,
    expected_stage: ExecutionStageName,
    expected_reason: str,
) -> None:
    paths = _workspace(tmp_path)
    outcome = compile_and_persist_workspace_plan(
        paths,
        config=RuntimeConfig(),
        requested_mode_id="default_codex_integrated",
    )
    assert outcome.active_plan is not None
    snapshot = _snapshot(plane=Plane.EXECUTION, stage=ExecutionStageName.INTEGRATOR)
    stage_result = _stage_result(
        stage=ExecutionStageName.INTEGRATOR,
        terminal_result=terminal_result,
    )

    decision = route_stage_result_from_graph(
        outcome.active_plan,
        snapshot,
        stage_result,
        RecoveryCounters(),
    )

    assert decision.action.value == "run_stage"
    assert decision.next_stage == expected_stage
    assert decision.reason == expected_reason


@pytest.mark.parametrize(
    ("metadata", "expected_failure_class"),
    (
        ({"failure_class": "network_unavailable"}, "network_unavailable"),
        ({}, "analyst_blocked"),
        ({"failure_class": "   "}, "analyst_blocked"),
    ),
)
def test_route_stage_result_from_graph_learning_blocked_sets_failure_class(
    tmp_path: Path,
    metadata: dict[str, object],
    expected_failure_class: str,
) -> None:
    paths = _workspace(tmp_path)
    outcome = compile_and_persist_workspace_plan(
        paths.root,
        config=RuntimeConfig(),
        requested_mode_id="learning_codex",
    )
    assert outcome.active_plan is not None

    snapshot = _snapshot(
        plane=Plane.LEARNING,
        stage=LearningStageName.ANALYST,
        work_item_kind=WorkItemKind.LEARNING_REQUEST,
        work_item_id="learn-001",
    )
    stage_result = StageResultEnvelope(
        run_id="run-001",
        plane=Plane.LEARNING,
        stage=LearningStageName.ANALYST,
        work_item_kind=WorkItemKind.LEARNING_REQUEST,
        work_item_id="learn-001",
        terminal_result=LearningTerminalResult.BLOCKED,
        result_class=ResultClass.BLOCKED,
        summary_status_marker="### BLOCKED",
        success=False,
        metadata=metadata,
        started_at=NOW,
        completed_at=NOW,
    )

    decision = route_stage_result_from_graph(
        outcome.active_plan,
        snapshot,
        stage_result,
        RecoveryCounters(),
    )

    assert decision.action.value == "blocked"
    assert decision.reason == "analyst_blocked"
    assert decision.failure_class == expected_failure_class


def test_route_stage_result_from_graph_rejects_invalid_closure_target_identity(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    engine = RuntimeEngine(paths, stage_runner=_unused_stage_runner)
    engine.startup()

    assert engine.compiled_plan is not None
    snapshot = _snapshot(
        plane=Plane.PLANNING,
        stage=PlanningStageName.ARBITER,
        work_item_kind=None,
        work_item_id=None,
    )
    stage_result = _stage_result(
        stage=PlanningStageName.ARBITER,
        terminal_result=PlanningTerminalResult.ARBITER_COMPLETE,
        work_item_kind=WorkItemKind.SPEC,
        work_item_id="spec-root-001",
        metadata={"request_kind": "closure_target"},
        closure_target=False,
    )

    with pytest.raises(ValueError, match="closure_target_root_spec_id"):
        route_stage_result_from_graph(
            engine.compiled_plan,
            snapshot,
            stage_result,
            RecoveryCounters(),
        )


def test_route_stage_result_from_graph_rejects_wrong_run_id(tmp_path: Path) -> None:
    """Prove stage results are rejected when run_id does not match the active run."""
    paths = _workspace(tmp_path)
    engine = RuntimeEngine(paths, stage_runner=_unused_stage_runner)
    engine.startup()

    assert engine.compiled_plan is not None
    snapshot = _snapshot(
        plane=Plane.EXECUTION,
        stage=ExecutionStageName.BUILDER,
    )
    stage_result = _stage_result(
        stage=ExecutionStageName.BUILDER,
        terminal_result=ExecutionTerminalResult.BUILDER_COMPLETE,
    ).model_copy(update={"run_id": "run-999"})

    with pytest.raises(ValueError, match="run_id"):
        route_stage_result_from_graph(
            engine.compiled_plan,
            snapshot,
            stage_result,
            RecoveryCounters(),
        )


def test_route_stage_result_from_graph_rejects_wrong_work_item_id(tmp_path: Path) -> None:
    """Prove stage results are rejected when work_item_id does not match the active run."""
    paths = _workspace(tmp_path)
    engine = RuntimeEngine(paths, stage_runner=_unused_stage_runner)
    engine.startup()

    assert engine.compiled_plan is not None
    snapshot = _snapshot(
        plane=Plane.EXECUTION,
        stage=ExecutionStageName.BUILDER,
    )
    stage_result = _stage_result(
        stage=ExecutionStageName.BUILDER,
        terminal_result=ExecutionTerminalResult.BUILDER_COMPLETE,
        work_item_id="task-999",
    )

    with pytest.raises(ValueError, match="work_item_id"):
        route_stage_result_from_graph(
            engine.compiled_plan,
            snapshot,
            stage_result,
            RecoveryCounters(),
        )


def test_route_stage_result_from_graph_rejects_wrong_stage(tmp_path: Path) -> None:
    """Prove stage results are rejected when stage does not match the active stage."""
    paths = _workspace(tmp_path)
    engine = RuntimeEngine(paths, stage_runner=_unused_stage_runner)
    engine.startup()

    assert engine.compiled_plan is not None
    snapshot = _snapshot(
        plane=Plane.EXECUTION,
        stage=ExecutionStageName.BUILDER,
    )
    stage_result = _stage_result(
        stage=ExecutionStageName.CHECKER,
        terminal_result=ExecutionTerminalResult.CHECKER_PASS,
    )

    with pytest.raises(ValueError, match="active_stage"):
        route_stage_result_from_graph(
            engine.compiled_plan,
            snapshot,
            stage_result,
            RecoveryCounters(),
        )


def test_route_stage_result_from_graph_rejects_wrong_node_id(tmp_path: Path) -> None:
    """Prove stage results are rejected when node_id does not match the active node."""
    paths = _workspace(tmp_path)
    engine = RuntimeEngine(paths, stage_runner=_unused_stage_runner)
    engine.startup()

    assert engine.compiled_plan is not None
    # Build snapshot where active_node_id differs from stage_result.node_id
    snapshot = _snapshot(
        plane=Plane.EXECUTION,
        stage=ExecutionStageName.BUILDER,
    )
    # The model_validator auto-sets active_node_id from active_stage, so we
    # build a stage_result with a different node_id to trigger the mismatch.
    stage_result = _stage_result(
        stage=ExecutionStageName.BUILDER,
        terminal_result=ExecutionTerminalResult.BUILDER_COMPLETE,
    ).model_copy(update={"node_id": "other_node"})

    with pytest.raises(ValueError, match="node_id"):
        route_stage_result_from_graph(
            engine.compiled_plan,
            snapshot,
            stage_result,
            RecoveryCounters(),
        )


def test_route_stage_result_from_graph_rejects_wrong_stage_kind_id(tmp_path: Path) -> None:
    """Prove stage results are rejected when stage_kind_id does not match the active kind."""
    paths = _workspace(tmp_path)
    engine = RuntimeEngine(paths, stage_runner=_unused_stage_runner)
    engine.startup()

    assert engine.compiled_plan is not None
    snapshot = _snapshot(
        plane=Plane.EXECUTION,
        stage=ExecutionStageName.BUILDER,
    )
    # The model_validator auto-sets active_stage_kind_id from active_stage
    stage_result = _stage_result(
        stage=ExecutionStageName.BUILDER,
        terminal_result=ExecutionTerminalResult.BUILDER_COMPLETE,
    ).model_copy(update={"stage_kind_id": "other_kind"})

    with pytest.raises(ValueError, match="stage_kind_id"):
        route_stage_result_from_graph(
            engine.compiled_plan,
            snapshot,
            stage_result,
            RecoveryCounters(),
        )


def test_route_stage_result_from_graph_rejects_wrong_work_item_family_id(tmp_path: Path) -> None:
    """Prove stage results are rejected when work_item_family_id does not match the active family."""
    paths = _workspace(tmp_path)
    engine = RuntimeEngine(paths, stage_runner=_unused_stage_runner)
    engine.startup()

    assert engine.compiled_plan is not None
    snapshot = _snapshot(
        plane=Plane.EXECUTION,
        stage=ExecutionStageName.BUILDER,
    )
    stage_result = _stage_result(
        stage=ExecutionStageName.BUILDER,
        terminal_result=ExecutionTerminalResult.BUILDER_COMPLETE,
    ).model_copy(update={"work_item_family_id": "other_family"})

    with pytest.raises(ValueError, match="work_item_family_id"):
        route_stage_result_from_graph(
            engine.compiled_plan,
            snapshot,
            stage_result,
            RecoveryCounters(),
        )


def test_route_stage_result_from_graph_routes_custom_node_in_canonical_execution_plane(
    tmp_path: Path,
) -> None:
    """
    Prove the generic router handles a custom node inside an existing canonical
    execution-plane graph, routing through the custom node to the next standard stage.
    """
    paths = _workspace(tmp_path)
    outcome = compile_and_persist_workspace_plan(
        paths.root,
        config=RuntimeConfig(),
        requested_mode_id="standard_plain",
    )
    assert outcome.active_plan is not None

    execution_graph = outcome.active_plan.execution_graph
    builder_node = next(
        node for node in execution_graph.nodes if node.node_id == "builder"
    )
    custom_node = builder_node.model_copy(
        update={
            "node_id": "custom_builder",
            "stage_kind_id": "builder",
        }
    )
    updated_graph = execution_graph.model_copy(
        update={
            "nodes": (
                *execution_graph.nodes,
                custom_node,
            ),
            "compiled_entries": (
                *execution_graph.compiled_entries,
                execution_graph.compiled_entries[0].model_copy(
                    update={
                        "node_id": "custom_builder",
                        "stage_kind_id": "builder",
                    }
                ),
            ),
            "transitions": (
                *execution_graph.transitions,
                execution_graph.transitions[0].model_copy(
                    update={
                        "edge_id": "builder-complete-custom-to-checker",
                        "from_node_id": "custom_builder",
                    }
                ),
            ),
            "compiled_transitions": (
                *execution_graph.compiled_transitions,
                execution_graph.compiled_transitions[0].model_copy(
                    update={
                        "source_node_id": "custom_builder",
                    }
                ),
            ),
        }
    )
    plan = outcome.active_plan.model_copy(
        update={
            "execution_graph": updated_graph,
            "graphs_by_plane": {
                **outcome.active_plan.graphs_by_plane,
                Plane.EXECUTION: updated_graph,
            },
        }
    )

    snapshot = _snapshot(
        plane=Plane.EXECUTION,
        stage=ExecutionStageName.BUILDER,
    ).model_copy(
        update={
            "active_node_id": "custom_builder",
            "active_stage_kind_id": "builder",
        }
    )
    stage_result = _stage_result(
        stage=ExecutionStageName.BUILDER,
        terminal_result=ExecutionTerminalResult.BUILDER_COMPLETE,
    ).model_copy(
        update={
            "node_id": "custom_builder",
            "stage_kind_id": "builder",
        }
    )

    decision = route_stage_result_from_graph(
        plan,
        snapshot,
        stage_result,
        RecoveryCounters(),
    )

    assert decision.action.value == "run_stage"
    assert decision.next_stage is ExecutionStageName.CHECKER
    assert decision.next_node_id == "checker"
    assert decision.reason == "custom_builder:BUILDER_COMPLETE"


def test_custom_node_id_survives_in_terminal_reason_fallback(
    tmp_path: Path,
) -> None:
    """
    Prove that when a node has a compiled ``node_id`` distinct from its
    canonical ``runtime_stage``, the fallback terminal reason uses the
    compiled node identity rather than the runtime stage-name string.

    Uses the simplest successful transition (BUILDER_COMPLETE → IDLE)
    so the reason is the bare ``node_id:terminal_result`` fallback.
    """
    paths = _workspace(tmp_path)
    outcome = compile_and_persist_workspace_plan(
        paths.root,
        config=RuntimeConfig(),
        requested_mode_id="standard_plain",
    )
    assert outcome.active_plan is not None

    execution_graph = outcome.active_plan.execution_graph
    builder_node = next(
        node for node in execution_graph.nodes if node.node_id == "builder"
    )
    # Custom node with distinct node_id but canonical runtime_stage
    custom_node = builder_node.model_copy(
        update={
            "node_id": "builder_v2",
            "stage_kind_id": "builder",
        }
    )
    updated_graph = execution_graph.model_copy(
        update={
            "nodes": (
                *execution_graph.nodes,
                custom_node,
            ),
            "compiled_transitions": (
                *execution_graph.compiled_transitions,
                next(
                    t for t in execution_graph.compiled_transitions
                    if t.source_node_id == "builder"
                    and t.outcome == ExecutionTerminalResult.BUILDER_COMPLETE.value
                ).model_copy(update={"source_node_id": "builder_v2"}),
            ),
        }
    )
    plan = outcome.active_plan.model_copy(
        update={
            "execution_graph": updated_graph,
            "graphs_by_plane": {
                **outcome.active_plan.graphs_by_plane,
                Plane.EXECUTION: updated_graph,
            },
        }
    )

    snapshot = _snapshot(
        plane=Plane.EXECUTION,
        stage=ExecutionStageName.BUILDER,
    ).model_copy(
        update={
            "active_node_id": "builder_v2",
            "active_stage_kind_id": "builder",
        }
    )
    stage_result = _stage_result(
        stage=ExecutionStageName.BUILDER,
        terminal_result=ExecutionTerminalResult.BUILDER_COMPLETE,
    ).model_copy(
        update={
            "node_id": "builder_v2",
            "stage_kind_id": "builder",
        }
    )

    decision = route_stage_result_from_graph(
        plan,
        snapshot,
        stage_result,
        RecoveryCounters(),
    )

    # Runtime stage stays canonical (BUILDER), but the route reason uses
    # the compiled node_id ("builder_v2") as fallback authority.
    assert decision.action.value == "run_stage"
    assert decision.reason == "builder_v2:BUILDER_COMPLETE"
