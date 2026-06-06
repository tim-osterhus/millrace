from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from millrace_ai.contracts import (
    ActiveRunState,
    ExecutionStageName,
    ExecutionTerminalResult,
    IncidentDecision,
    IncidentDocument,
    Plane,
    PlanningStageName,
    ProbeDocument,
    ResultClass,
    SpecDocument,
    StageResultEnvelope,
    TaskDocument,
    WorkItemKind,
)
from millrace_ai.paths import bootstrap_workspace, workspace_paths
from millrace_ai.queue_store import QueueStore
from millrace_ai.router import RouterAction, RouterDecision
from millrace_ai.runtime import RuntimeEngine
from millrace_ai.runtime.lanes import compiled_plan_fingerprint_for_runtime
from millrace_ai.runtime.result_application import apply_router_decision, route_stage_result
from millrace_ai.state_store import load_snapshot, save_snapshot
from millrace_ai.work_documents import read_work_document_as, render_work_document
from millrace_ai.workspace.work_item_adapters import adapter_for_family_id

NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


def _workspace(tmp_path: Path):
    return bootstrap_workspace(workspace_paths(tmp_path / "workspace"))


def _unused_stage_runner(_request):
    raise AssertionError("stage runner should not be called")


def _task_doc(task_id: str) -> TaskDocument:
    return TaskDocument(
        task_id=task_id,
        title=f"Task {task_id}",
        summary="terminal-action application test",
        target_paths=("src/millrace_ai/runtime/result_application.py",),
        acceptance=("terminal actions drive source lifecycle mutation",),
        required_checks=("pytest tests/runtime/test_terminal_action_application.py -q",),
        references=("tests/runtime/test_terminal_action_application.py",),
        risk=("source queue state drift",),
        created_at=NOW,
        created_by="tests",
    )


def _spec_doc(spec_id: str) -> SpecDocument:
    return SpecDocument(
        spec_id=spec_id,
        title=f"Spec {spec_id}",
        summary="terminal-action application test",
        source_type="manual",
        goals=("validate terminal action source lifecycle",),
        constraints=("stay deterministic",),
        acceptance=("terminal action applies to spec source",),
        references=("tests/runtime/test_terminal_action_application.py",),
        created_at=NOW,
        created_by="tests",
    )


def _probe_doc(probe_id: str) -> ProbeDocument:
    return ProbeDocument(
        probe_id=probe_id,
        title=f"Probe {probe_id}",
        summary="terminal-action application test",
        request="Validate terminal action source lifecycle.",
        created_at=NOW,
        created_by="tests",
    )


def _incident_doc(incident_id: str) -> IncidentDocument:
    return IncidentDocument(
        incident_id=incident_id,
        title=f"Incident {incident_id}",
        summary="terminal-action application test",
        source_stage=PlanningStageName.AUDITOR,
        source_plane=Plane.PLANNING,
        failure_class="terminal_action_test",
        trigger_reason="terminal action application",
        consultant_decision=IncidentDecision.NEEDS_PLANNING,
        opened_at=NOW,
        opened_by="tests",
    )


def _document_for_family(family_id: str, item_id: str):
    if family_id == "task":
        return _task_doc(item_id)
    if family_id == "spec":
        return _spec_doc(item_id)
    if family_id == "probe":
        return _probe_doc(item_id)
    if family_id == "incident":
        return _incident_doc(item_id)
    raise AssertionError(f"unexpected family {family_id}")


def _put_active_work_item(paths, *, family_id: str, item_id: str) -> None:
    adapter = adapter_for_family_id(family_id)
    active_path = adapter.active_dir(paths) / f"{item_id}.md"
    active_path.write_text(
        render_work_document(_document_for_family(family_id, item_id)),
        encoding="utf-8",
    )


def _engine_with_active_task(
    tmp_path: Path,
    *,
    stage: ExecutionStageName = ExecutionStageName.UPDATER,
) -> RuntimeEngine:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_task(_task_doc("task-001"))
    assert queue.claim_next_execution_task() is not None
    engine = RuntimeEngine(paths, stage_runner=_unused_stage_runner)
    engine.startup()
    assert engine.snapshot is not None
    assert engine.compiled_plan is not None
    active_run = ActiveRunState(
        plane=Plane.EXECUTION,
        lane_id="execution.main",
        stage=stage,
        node_id=stage.value,
        stage_kind_id=stage.value,
        run_id="run-terminal",
        compiled_plan_id=engine.compiled_plan.compiled_plan_id,
        compiled_plan_fingerprint=compiled_plan_fingerprint_for_runtime(engine.compiled_plan),
        request_kind="active_work_item",
        work_item_kind=WorkItemKind.TASK,
        work_item_id="task-001",
        active_since=NOW,
    )
    engine.snapshot = engine.snapshot.model_copy(
        update={
            "active_runs_by_plane": {Plane.EXECUTION: active_run},
            "active_plane": Plane.EXECUTION,
            "active_stage": stage,
            "active_node_id": stage.value,
            "active_stage_kind_id": stage.value,
            "active_run_id": "run-terminal",
            "active_work_item_kind": WorkItemKind.TASK,
            "active_work_item_id": "task-001",
            "active_since": NOW,
            "updated_at": NOW,
        }
    )
    save_snapshot(paths, engine.snapshot)
    return engine


@pytest.mark.parametrize("family_id", ("task", "spec", "probe", "incident"))
def test_complete_work_item_action_applies_to_builtin_source_families(
    tmp_path: Path,
    family_id: str,
) -> None:
    paths = _workspace(tmp_path)
    item_id = f"{family_id}-001"
    _put_active_work_item(paths, family_id=family_id, item_id=item_id)
    engine = RuntimeEngine(paths, stage_runner=_unused_stage_runner)
    engine.startup()
    adapter = adapter_for_family_id(family_id)
    stage_result = StageResultEnvelope(
        run_id="run-terminal",
        plane=Plane.PLANNING,
        stage=PlanningStageName.ARBITER,
        node_id="arbiter",
        stage_kind_id="arbiter",
        work_item_kind=adapter.work_item_kind,
        work_item_family_id=family_id,
        work_item_id=item_id,
        terminal_result="TEST_COMPLETE",
        result_class=ResultClass.SUCCESS,
        summary_status_marker="### TEST_COMPLETE",
        success=True,
        started_at=NOW,
        completed_at=NOW,
    )
    decision = RouterDecision(
        action=RouterAction.IDLE,
        next_plane=None,
        next_stage=None,
        reason="test_complete_any_source_family",
        terminal_state_id="test_complete",
        terminal_action_id="complete_work_item",
        terminal_action_router_consequence="idle",
        lifecycle_mutation_plan_id="complete_work_item",
        lifecycle_action_id="complete",
    )

    apply_router_decision(engine, decision, stage_result)

    assert (adapter.done_dir(paths) / f"{item_id}.md").is_file()
    assert not (adapter.active_dir(paths) / f"{item_id}.md").exists()


@pytest.mark.parametrize("family_id", ("task", "spec", "probe", "incident"))
def test_block_work_item_action_applies_to_builtin_source_families(
    tmp_path: Path,
    family_id: str,
) -> None:
    paths = _workspace(tmp_path)
    item_id = f"{family_id}-001"
    _put_active_work_item(paths, family_id=family_id, item_id=item_id)
    engine = RuntimeEngine(paths, stage_runner=_unused_stage_runner)
    engine.startup()
    adapter = adapter_for_family_id(family_id)
    stage_result = StageResultEnvelope(
        run_id="run-terminal",
        plane=Plane.PLANNING,
        stage=PlanningStageName.ARBITER,
        node_id="arbiter",
        stage_kind_id="arbiter",
        work_item_kind=adapter.work_item_kind,
        work_item_family_id=family_id,
        work_item_id=item_id,
        terminal_result="TEST_BLOCKED",
        result_class=ResultClass.BLOCKED,
        summary_status_marker="### TEST_BLOCKED",
        success=False,
        started_at=NOW,
        completed_at=NOW,
    )
    decision = RouterDecision(
        action=RouterAction.BLOCKED,
        next_plane=None,
        next_stage=None,
        reason="test_block_any_source_family",
        terminal_state_id="test_blocked",
        terminal_action_id="block_work_item",
        terminal_action_router_consequence="blocked",
        lifecycle_mutation_plan_id="block_work_item",
        lifecycle_action_id="block",
    )

    apply_router_decision(engine, decision, stage_result)

    assert (adapter.blocked_dir(paths) / f"{item_id}.md").is_file()
    assert not (adapter.active_dir(paths) / f"{item_id}.md").exists()


def test_idle_router_decision_applies_resolved_terminal_lifecycle_action(
    tmp_path: Path,
) -> None:
    engine = _engine_with_active_task(tmp_path)
    paths = engine.paths
    stage_result = StageResultEnvelope(
        run_id="run-terminal",
        plane=Plane.EXECUTION,
        stage=ExecutionStageName.UPDATER,
        node_id="updater",
        stage_kind_id="updater",
        work_item_kind=WorkItemKind.TASK,
        work_item_id="task-001",
        terminal_result=ExecutionTerminalResult.UPDATE_COMPLETE,
        result_class=ResultClass.SUCCESS,
        summary_status_marker="### UPDATE_COMPLETE",
        success=True,
        started_at=NOW,
        completed_at=NOW,
    )
    decision = RouterDecision(
        action=RouterAction.IDLE,
        next_plane=None,
        next_stage=None,
        reason="test_terminal_lifecycle",
        terminal_state_id="test_blocking_success",
        terminal_action_id="test_block_source",
        terminal_action_router_consequence="idle",
        lifecycle_mutation_plan_id="block_work_item",
        lifecycle_action_id="block",
    )

    apply_router_decision(engine, decision, stage_result)

    snapshot = load_snapshot(paths)
    assert (paths.tasks_blocked_dir / "task-001.md").is_file()
    assert not (paths.tasks_done_dir / "task-001.md").exists()
    assert snapshot.active_stage is None
    assert snapshot.execution_status_marker == "### UPDATE_COMPLETE"


def test_graph_authority_idle_decision_without_lifecycle_authority_fails(
    tmp_path: Path,
) -> None:
    engine = _engine_with_active_task(tmp_path)
    paths = engine.paths
    stage_result = StageResultEnvelope(
        run_id="run-terminal",
        plane=Plane.EXECUTION,
        stage=ExecutionStageName.UPDATER,
        node_id="updater",
        stage_kind_id="updater",
        work_item_kind=WorkItemKind.TASK,
        work_item_id="task-001",
        terminal_result=ExecutionTerminalResult.UPDATE_COMPLETE,
        result_class=ResultClass.SUCCESS,
        summary_status_marker="### UPDATE_COMPLETE",
        success=True,
        started_at=NOW,
        completed_at=NOW,
    )
    decision = RouterDecision(
        action=RouterAction.IDLE,
        next_plane=None,
        next_stage=None,
        reason="missing_terminal_lifecycle",
        terminal_state_id="update_complete",
        terminal_action_id="complete_work_item",
        terminal_action_router_consequence="idle",
    )

    with pytest.raises(ValueError, match="lacks lifecycle mutation metadata"):
        apply_router_decision(engine, decision, stage_result)

    assert (paths.tasks_active_dir / "task-001.md").is_file()
    assert not (paths.tasks_done_dir / "task-001.md").exists()


def test_graph_authority_idle_decision_with_plan_but_no_lifecycle_action_fails(
    tmp_path: Path,
) -> None:
    engine = _engine_with_active_task(tmp_path)
    paths = engine.paths
    stage_result = StageResultEnvelope(
        run_id="run-terminal",
        plane=Plane.EXECUTION,
        stage=ExecutionStageName.UPDATER,
        node_id="updater",
        stage_kind_id="updater",
        work_item_kind=WorkItemKind.TASK,
        work_item_id="task-001",
        terminal_result=ExecutionTerminalResult.UPDATE_COMPLETE,
        result_class=ResultClass.SUCCESS,
        summary_status_marker="### UPDATE_COMPLETE",
        success=True,
        started_at=NOW,
        completed_at=NOW,
    )
    decision = RouterDecision(
        action=RouterAction.IDLE,
        next_plane=None,
        next_stage=None,
        reason="missing_terminal_lifecycle_action",
        terminal_state_id="update_complete",
        terminal_action_id="complete_work_item",
        terminal_action_router_consequence="idle",
        lifecycle_mutation_plan_id="complete_work_item",
    )

    with pytest.raises(ValueError, match="lacks resolved lifecycle action metadata"):
        apply_router_decision(engine, decision, stage_result)

    assert (paths.tasks_active_dir / "task-001.md").is_file()
    assert not (paths.tasks_done_dir / "task-001.md").exists()


def test_graph_authority_blocked_decision_without_lifecycle_authority_fails(
    tmp_path: Path,
) -> None:
    engine = _engine_with_active_task(tmp_path, stage=ExecutionStageName.CONSULTANT)
    paths = engine.paths
    stage_result = StageResultEnvelope(
        run_id="run-terminal",
        plane=Plane.EXECUTION,
        stage=ExecutionStageName.CONSULTANT,
        node_id="consultant",
        stage_kind_id="consultant",
        work_item_kind=WorkItemKind.TASK,
        work_item_id="task-001",
        terminal_result=ExecutionTerminalResult.BLOCKED,
        result_class=ResultClass.BLOCKED,
        summary_status_marker="### BLOCKED",
        success=False,
        started_at=NOW,
        completed_at=NOW,
    )
    decision = RouterDecision(
        action=RouterAction.BLOCKED,
        next_plane=None,
        next_stage=None,
        reason="missing_terminal_lifecycle",
        terminal_state_id="blocked",
        terminal_action_id="block_work_item",
        terminal_action_router_consequence="blocked",
        failure_class="consultant_blocked",
    )

    with pytest.raises(ValueError, match="lacks lifecycle mutation metadata"):
        apply_router_decision(engine, decision, stage_result)

    assert (paths.tasks_active_dir / "task-001.md").is_file()
    assert not (paths.tasks_blocked_dir / "task-001.md").exists()


def test_graph_authority_blocked_decision_with_plan_but_no_lifecycle_action_fails(
    tmp_path: Path,
) -> None:
    engine = _engine_with_active_task(tmp_path, stage=ExecutionStageName.CONSULTANT)
    paths = engine.paths
    stage_result = StageResultEnvelope(
        run_id="run-terminal",
        plane=Plane.EXECUTION,
        stage=ExecutionStageName.CONSULTANT,
        node_id="consultant",
        stage_kind_id="consultant",
        work_item_kind=WorkItemKind.TASK,
        work_item_id="task-001",
        terminal_result=ExecutionTerminalResult.BLOCKED,
        result_class=ResultClass.BLOCKED,
        summary_status_marker="### BLOCKED",
        success=False,
        started_at=NOW,
        completed_at=NOW,
    )
    decision = RouterDecision(
        action=RouterAction.BLOCKED,
        next_plane=None,
        next_stage=None,
        reason="missing_terminal_lifecycle_action",
        terminal_state_id="blocked",
        terminal_action_id="block_work_item",
        terminal_action_router_consequence="blocked",
        lifecycle_mutation_plan_id="block_work_item",
        failure_class="consultant_blocked",
    )

    with pytest.raises(ValueError, match="lacks resolved lifecycle action metadata"):
        apply_router_decision(engine, decision, stage_result)

    assert (paths.tasks_active_dir / "task-001.md").is_file()
    assert not (paths.tasks_blocked_dir / "task-001.md").exists()


def test_explicit_non_mutating_terminal_action_clears_runtime_without_source_mutation(
    tmp_path: Path,
) -> None:
    engine = _engine_with_active_task(tmp_path)
    paths = engine.paths
    stage_result = StageResultEnvelope(
        run_id="run-terminal",
        plane=Plane.EXECUTION,
        stage=ExecutionStageName.UPDATER,
        node_id="updater",
        stage_kind_id="updater",
        work_item_kind=WorkItemKind.TASK,
        work_item_id="task-001",
        terminal_result=ExecutionTerminalResult.UPDATE_COMPLETE,
        result_class=ResultClass.SUCCESS,
        summary_status_marker="### UPDATE_COMPLETE",
        success=True,
        started_at=NOW,
        completed_at=NOW,
    )
    decision = RouterDecision(
        action=RouterAction.IDLE,
        next_plane=None,
        next_stage=None,
        reason="explicit_non_mutating",
        terminal_state_id="idle_plane",
        terminal_action_id="idle_plane",
        terminal_action_router_consequence="idle",
        terminal_action_non_mutating=True,
    )

    apply_router_decision(engine, decision, stage_result)

    snapshot = load_snapshot(paths)
    assert (paths.tasks_active_dir / "task-001.md").is_file()
    assert not (paths.tasks_done_dir / "task-001.md").exists()
    assert snapshot.active_stage is None
    assert snapshot.execution_status_marker == "### UPDATE_COMPLETE"


def test_handoff_result_uses_terminal_action_failure_class_for_incident(
    tmp_path: Path,
) -> None:
    engine = _engine_with_active_task(tmp_path, stage=ExecutionStageName.CONSULTANT)
    paths = engine.paths
    stage_result = StageResultEnvelope(
        run_id="run-terminal",
        plane=Plane.EXECUTION,
        stage=ExecutionStageName.CONSULTANT,
        node_id="consultant",
        stage_kind_id="consultant",
        work_item_kind=WorkItemKind.TASK,
        work_item_id="task-001",
        terminal_result=ExecutionTerminalResult.NEEDS_PLANNING,
        result_class=ResultClass.ESCALATE_PLANNING,
        summary_status_marker="### NEEDS_PLANNING",
        success=False,
        started_at=NOW,
        completed_at=NOW,
    )

    decision = route_stage_result(engine, stage_result)
    spawned = apply_router_decision(engine, decision, stage_result)

    assert decision.terminal_action_id == "escalate_to_planning"
    assert decision.failure_class == "terminal_escalate_planning"
    assert (paths.tasks_blocked_dir / "task-001.md").is_file()
    assert len(spawned) == 1
    incident = read_work_document_as(spawned[0], model=IncidentDocument)
    assert incident.failure_class == "terminal_escalate_planning"


# ── minimal three-plane fixture runtime tests ───────────────────────────────


def _minimal_three_plane_engine(paths) -> RuntimeEngine:
    engine = RuntimeEngine(
        paths, stage_runner=_unused_stage_runner, mode_id="minimal_three_plane"
    )
    engine.startup()
    assert engine.snapshot is not None
    assert engine.compiled_plan is not None
    return engine


def test_minimal_three_plane_fixture_planning_complete_work_item(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_spec(_spec_doc("spec-001"))
    assert queue.claim_next_planning_item() is not None

    engine = _minimal_three_plane_engine(paths)
    assert engine.snapshot is not None
    assert engine.compiled_plan is not None

    engine.snapshot = engine.snapshot.model_copy(
        update={
            "active_runs_by_plane": {
                Plane.PLANNING: ActiveRunState(
                    plane=Plane.PLANNING,
                    stage=PlanningStageName.PLANNER,
                    node_id="basic_planner",
                    stage_kind_id="basic_planner",
                    run_id="run-mtp-plan-complete",
                    compiled_plan_id=engine.compiled_plan.compiled_plan_id,
                    compiled_plan_fingerprint=compiled_plan_fingerprint_for_runtime(
                        engine.compiled_plan
                    ),
                    request_kind="active_work_item",
                    work_item_kind=WorkItemKind.SPEC,
                    work_item_id="spec-001",
                    active_since=NOW,
                )
            },
            "active_plane": Plane.PLANNING,
            "active_stage": PlanningStageName.PLANNER,
            "active_node_id": "basic_planner",
            "active_stage_kind_id": "basic_planner",
            "active_run_id": "run-mtp-plan-complete",
            "active_work_item_kind": WorkItemKind.SPEC,
            "active_work_item_id": "spec-001",
            "active_since": NOW,
            "updated_at": NOW,
        }
    )
    save_snapshot(paths, engine.snapshot)

    stage_result = StageResultEnvelope(
        run_id="run-mtp-plan-complete",
        plane="planning",
        stage="planner",
        node_id="basic_planner",
        stage_kind_id="basic_planner",
        work_item_kind=WorkItemKind.SPEC,
        work_item_id="spec-001",
        terminal_result="BASIC_PLANNING_COMPLETE",
        result_class=ResultClass.SUCCESS,
        summary_status_marker="### BASIC_PLANNING_COMPLETE",
        success=True,
        started_at=NOW,
        completed_at=NOW,
    )

    decision = route_stage_result(engine, stage_result)
    apply_router_decision(engine, decision, stage_result)

    assert (paths.specs_done_dir / "spec-001.md").is_file()
    assert not (paths.specs_active_dir / "spec-001.md").exists()


def test_minimal_three_plane_fixture_planning_block_work_item(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_spec(_spec_doc("spec-001"))
    assert queue.claim_next_planning_item() is not None

    engine = _minimal_three_plane_engine(paths)
    assert engine.snapshot is not None
    assert engine.compiled_plan is not None

    engine.snapshot = engine.snapshot.model_copy(
        update={
            "active_runs_by_plane": {
                Plane.PLANNING: ActiveRunState(
                    plane=Plane.PLANNING,
                    stage=PlanningStageName.PLANNER,
                    node_id="basic_planner",
                    stage_kind_id="basic_planner",
                    run_id="run-mtp-plan-block",
                    compiled_plan_id=engine.compiled_plan.compiled_plan_id,
                    compiled_plan_fingerprint=compiled_plan_fingerprint_for_runtime(
                        engine.compiled_plan
                    ),
                    request_kind="active_work_item",
                    work_item_kind=WorkItemKind.SPEC,
                    work_item_id="spec-001",
                    active_since=NOW,
                )
            },
            "active_plane": Plane.PLANNING,
            "active_stage": PlanningStageName.PLANNER,
            "active_node_id": "basic_planner",
            "active_stage_kind_id": "basic_planner",
            "active_run_id": "run-mtp-plan-block",
            "active_work_item_kind": WorkItemKind.SPEC,
            "active_work_item_id": "spec-001",
            "active_since": NOW,
            "updated_at": NOW,
        }
    )
    save_snapshot(paths, engine.snapshot)

    stage_result = StageResultEnvelope(
        run_id="run-mtp-plan-block",
        plane="planning",
        stage="planner",
        node_id="basic_planner",
        stage_kind_id="basic_planner",
        work_item_kind=WorkItemKind.SPEC,
        work_item_id="spec-001",
        terminal_result="BASIC_PLANNING_BLOCKED",
        result_class=ResultClass.BLOCKED,
        summary_status_marker="### BASIC_PLANNING_BLOCKED",
        success=False,
        started_at=NOW,
        completed_at=NOW,
    )

    decision = route_stage_result(engine, stage_result)
    apply_router_decision(engine, decision, stage_result)

    assert (paths.specs_blocked_dir / "spec-001.md").is_file()
    assert not (paths.specs_active_dir / "spec-001.md").exists()
