from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from millrace_ai.contracts import (
    IncidentDocument,
    PlanningStageName,
    ResultClass,
    SpecDocument,
    StageResultEnvelope,
    TaskDocument,
    WorkItemKind,
)
from millrace_ai.events import read_runtime_events
from millrace_ai.paths import bootstrap_workspace, workspace_paths
from millrace_ai.queue_store import QueueStore
from millrace_ai.router import RouterAction, RouterDecision
from millrace_ai.runtime import effect_execution
from millrace_ai.runtime.effect_execution import apply_runtime_effect_for_stage_result
from millrace_ai.runtime.effects import (
    RuntimeEffectDecision,
    RuntimeEffectMutationPhase,
    RuntimeEffectResult,
    SourceLifecycleAction,
    SourceLifecycleIntent,
    apply_runtime_effect_result,
    lifecycle_intent_for_terminal_result,
)
from millrace_ai.runtime.engine import RuntimeEngine

NOW = datetime(2026, 5, 19, tzinfo=timezone.utc)


def _workspace(tmp_path: Path):
    return bootstrap_workspace(workspace_paths(tmp_path / "workspace"))


def _task_doc(task_id: str = "task-001") -> TaskDocument:
    return TaskDocument(
        task_id=task_id,
        title="Task",
        target_paths=("src/millrace_ai/runtime/",),
        acceptance=("Runtime effect tests pass.",),
        required_checks=("pytest tests/runtime/test_runtime_effects.py -q",),
        references=("lab/tasks/queue/2026-05-19-v020-07-runtime-effects-and-lifecycle-interpreter.md",),
        risk=("Source lifecycle ordering.",),
        created_at=NOW,
        created_by="tests",
    )


def _spec_doc(spec_id: str = "spec-child-001") -> SpecDocument:
    return SpecDocument(
        spec_id=spec_id,
        title="Child Spec",
        summary="Planner emitted child spec.",
        source_type="incident",
        source_id="incident-001",
        parent_spec_id="spec-root-001",
        root_idea_id="idea-001",
        root_spec_id="spec-root-001",
        goals=("Remediate the incident through a child spec.",),
        constraints=("Keep planner disposition runtime-visible.",),
        acceptance=("Child spec remains queued after the source incident resolves.",),
        references=("millrace-agents/incidents/active/incident-001.md",),
        created_at=NOW,
        created_by="planner",
    )


def _incident_doc(incident_id: str = "incident-001") -> IncidentDocument:
    return IncidentDocument(
        incident_id=incident_id,
        title="Planner Incident",
        summary="Planner should emit a child remediation spec.",
        root_idea_id="idea-001",
        root_spec_id="spec-root-001",
        source_stage="arbiter",
        source_plane="planning",
        failure_class="arbiter_parity_gap",
        trigger_reason="Arbiter requested remediation.",
        consultant_decision="needs_planning",
        opened_at=NOW,
        opened_by="tests",
    )


def _stage_result(task_id: str = "task-001") -> StageResultEnvelope:
    return StageResultEnvelope(
        run_id="run-001",
        plane="execution",
        stage="builder",
        node_id="builder",
        stage_kind_id="builder",
        work_item_kind="task",
        work_item_id=task_id,
        terminal_result="BUILDER_COMPLETE",
        result_class=ResultClass.SUCCESS,
        summary_status_marker="### BUILDER_COMPLETE",
        success=True,
        started_at=NOW,
        completed_at=NOW,
    )


def _planner_stage_result(
    *,
    work_item_family_id: str = "incident",
    work_item_kind: WorkItemKind = WorkItemKind.INCIDENT,
    work_item_id: str = "incident-001",
    terminal_result: str = "PLANNER_COMPLETE",
    result_class: ResultClass = ResultClass.SUCCESS,
    success: bool = True,
) -> StageResultEnvelope:
    return StageResultEnvelope(
        run_id="run-planner",
        plane="planning",
        stage="planner",
        node_id="planner",
        stage_kind_id="planner",
        work_item_family_id=work_item_family_id,
        work_item_kind=work_item_kind,
        work_item_id=work_item_id,
        terminal_result=terminal_result,
        result_class=result_class,
        summary_status_marker=f"### {terminal_result}",
        success=success,
        started_at=NOW,
        completed_at=NOW,
    )


def _blueprint_approval_stage_result() -> StageResultEnvelope:
    return StageResultEnvelope(
        run_id="run-blueprint",
        plane="planning",
        stage="manager",
        node_id="evaluator_blueprint",
        stage_kind_id="evaluator_blueprint",
        work_item_kind="blueprint_draft",
        work_item_id="draft-blueprint-001",
        terminal_result="BLUEPRINT_APPROVED",
        result_class=ResultClass.SUCCESS,
        summary_status_marker="### BLUEPRINT_APPROVED",
        success=True,
        started_at=NOW,
        completed_at=NOW,
    )


def _manager_blueprint_stage_result(
    *,
    work_item_family_id: str = "spec",
    work_item_kind: WorkItemKind = WorkItemKind.SPEC,
    work_item_id: str = "spec-blueprint-001",
) -> StageResultEnvelope:
    return StageResultEnvelope(
        run_id="run-manager-blueprint",
        plane="planning",
        stage="manager",
        node_id="manager_blueprint",
        stage_kind_id="manager_blueprint",
        work_item_family_id=work_item_family_id,
        work_item_kind=work_item_kind,
        work_item_id=work_item_id,
        terminal_result="MANAGER_BLUEPRINT_COMPLETE",
        result_class=ResultClass.SUCCESS,
        summary_status_marker="### MANAGER_BLUEPRINT_COMPLETE",
        success=True,
        started_at=NOW,
        completed_at=NOW,
    )


def _unused_stage_runner(request):  # pragma: no cover - defensive guard
    raise AssertionError(f"stage runner should not be called by this test: {request}")


def _runtime_failure_policy(**updates) -> SimpleNamespace:
    policy = {
        "policy_id": "test_require_operator_policy",
        "applies_to_origins": ("runtime_effect",),
        "applies_to_planes": ("planning",),
        "applies_to_families": ("spec",),
        "applies_to_failure_classes": ("blueprint_manifest_duplicate",),
        "applies_to_mutation_phases": ("pre_mutation",),
        "applies_to_handler_ids": ("manager_blueprint_manifest_to_blueprint_drafts",),
        "applies_to_source_node_ids": ("manager_blueprint",),
        "applies_to_source_terminal_state_ids": ("manager_blueprint_complete",),
        "action": "require_operator",
        "target_node_id": None,
        "target_terminal_state_id": None,
        "max_attempts": None,
        "incident_severity": None,
    }
    policy.update(updates)
    return SimpleNamespace(**policy)


def _write_planner_disposition(
    run_dir: Path,
    *,
    source_work_item_family_id: str,
    source_work_item_id: str,
    disposition: str,
    emitted_spec_ids: tuple[str, ...] = (),
    refined_active_source: bool = False,
) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "planner_disposition.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "kind": "planner_disposition",
                "source_work_item_family_id": source_work_item_family_id,
                "source_work_item_id": source_work_item_id,
                "disposition": disposition,
                "emitted_spec_ids": list(emitted_spec_ids),
                "refined_active_source": refined_active_source,
                "recommended_next_action": disposition,
                "created_at": NOW.isoformat().replace("+00:00", "Z"),
                "created_by": "planner",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def test_runtime_effect_result_contract_accepts_lifecycle_intent() -> None:
    result = RuntimeEffectResult(
        handler_id="generated_task_artifact_to_task_queue",
        decision=RuntimeEffectDecision.REQUEST_COMPLETE_SOURCE,
        created_paths=("millrace-agents/tasks/queue/task-002.md",),
        source_lifecycle_intent=SourceLifecycleIntent(
            lifecycle_plan_id="complete_source_after_effect",
            action=SourceLifecycleAction.COMPLETE,
            work_item_kind=WorkItemKind.TASK,
            work_item_id="task-001",
        ),
    )

    assert result.decision is RuntimeEffectDecision.REQUEST_COMPLETE_SOURCE
    assert result.source_lifecycle_intent is not None
    assert result.source_lifecycle_intent.work_item_kind is WorkItemKind.TASK
    assert result.mutation_phase is RuntimeEffectMutationPhase.UNKNOWN


def test_lifecycle_intent_for_terminal_result_maps_success_and_blocked() -> None:
    complete_intent = lifecycle_intent_for_terminal_result(
        _stage_result(),
        lifecycle_plan_id="complete_work_item",
    )
    blocked_intent = lifecycle_intent_for_terminal_result(
        _stage_result().model_copy(
            update={
                "terminal_result": "BLOCKED",
                "result_class": ResultClass.BLOCKED,
                "success": False,
            }
        ),
        lifecycle_plan_id="block_work_item",
    )

    assert complete_intent.action is SourceLifecycleAction.COMPLETE
    assert blocked_intent.action is SourceLifecycleAction.BLOCK


def test_effect_applies_source_lifecycle_after_destination_artifacts_exist(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_task(_task_doc())
    assert queue.claim_next_execution_task() is not None
    created_path = paths.tasks_queue_dir / "task-002.md"
    created_path.write_text("queued task artifact", encoding="utf-8")

    result = RuntimeEffectResult(
        handler_id="generated_task_artifact_to_task_queue",
        decision=RuntimeEffectDecision.REQUEST_COMPLETE_SOURCE,
        created_paths=(str(created_path.relative_to(paths.root)),),
        source_lifecycle_intent=SourceLifecycleIntent(
            lifecycle_plan_id="complete_source_after_effect",
            action=SourceLifecycleAction.COMPLETE,
            work_item_kind=WorkItemKind.TASK,
            work_item_id="task-001",
        ),
    )

    applied = apply_runtime_effect_result(paths, result)

    assert applied.decision is RuntimeEffectDecision.REQUEST_COMPLETE_SOURCE
    assert (paths.tasks_done_dir / "task-001.md").is_file()
    assert created_path.is_file()


def test_effect_missing_destination_does_not_move_source_and_records_failure(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_task(_task_doc())
    assert queue.claim_next_execution_task() is not None

    result = RuntimeEffectResult(
        handler_id="generated_task_artifact_to_task_queue",
        decision=RuntimeEffectDecision.REQUEST_COMPLETE_SOURCE,
        created_paths=("millrace-agents/tasks/queue/task-002.md",),
        source_lifecycle_intent=SourceLifecycleIntent(
            lifecycle_plan_id="complete_source_after_effect",
            action=SourceLifecycleAction.COMPLETE,
            work_item_kind=WorkItemKind.TASK,
            work_item_id="task-001",
        ),
    )

    applied = apply_runtime_effect_result(paths, result)

    assert applied.decision is RuntimeEffectDecision.RETRY_RECOVERY
    assert applied.failure_class == "runtime_effect_destination_missing"
    assert (paths.tasks_active_dir / "task-001.md").is_file()
    assert not (paths.tasks_done_dir / "task-001.md").exists()
    events = read_runtime_events(paths)
    assert events[-1].event_type == "runtime_effect_destination_missing"
    assert events[-1].data["lifecycle_plan_id"] == "complete_source_after_effect"


def test_stage_effect_selection_obeys_compiled_runtime_effect_rules(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    engine = RuntimeEngine(paths, stage_runner=_unused_stage_runner)
    engine.startup()
    assert engine.compiled_plan is not None
    engine.compiled_plan = engine.compiled_plan.model_copy(update={"runtime_effect_rules": ()})

    stage_result = StageResultEnvelope(
        run_id="run-001",
        plane="planning",
        stage="manager",
        node_id="manager_blueprint",
        stage_kind_id="manager_blueprint",
        work_item_kind="spec",
        work_item_id="spec-001",
        terminal_result="MANAGER_BLUEPRINT_COMPLETE",
        result_class=ResultClass.SUCCESS,
        summary_status_marker="### MANAGER_BLUEPRINT_COMPLETE",
        success=True,
        started_at=NOW,
        completed_at=NOW,
    )
    router_decision = RouterDecision(
        action=RouterAction.IDLE,
        next_plane=None,
        next_stage=None,
        reason="test_route",
    )

    application = apply_runtime_effect_for_stage_result(
        engine,
        request=SimpleNamespace(run_dir=str(tmp_path / "run")),
        stage_result=stage_result,
        router_decision=router_decision,
    )

    assert application.router_decision is router_decision
    assert application.spawned_paths == ()
    assert application.source_lifecycle_applied is False
    assert stage_result.metadata == {}


@pytest.mark.parametrize(
    ("mode_id", "next_node_id", "next_stage_kind_id"),
    [
        (None, "manager", "manager"),
        ("blueprint_codex", "manager_blueprint", "manager_blueprint"),
    ],
)
def test_planner_disposition_active_source_ready_continues_existing_route(
    tmp_path: Path,
    mode_id: str | None,
    next_node_id: str,
    next_stage_kind_id: str,
) -> None:
    paths = _workspace(tmp_path)
    engine = RuntimeEngine(paths, stage_runner=_unused_stage_runner, mode_id=mode_id)
    engine.startup()
    run_dir = tmp_path / "run"
    _write_planner_disposition(
        run_dir,
        source_work_item_family_id="spec",
        source_work_item_id="spec-001",
        disposition="active_source_ready_for_manager",
        refined_active_source=True,
    )
    stage_result = _planner_stage_result(
        work_item_family_id="spec",
        work_item_kind=WorkItemKind.SPEC,
        work_item_id="spec-001",
    )
    router_decision = RouterDecision(
        action=RouterAction.RUN_STAGE,
        next_plane=stage_result.plane,
        next_stage=PlanningStageName.MANAGER,
        next_node_id=next_node_id,
        next_stage_kind_id=next_stage_kind_id,
        reason="planner:PLANNER_COMPLETE",
    )

    application = apply_runtime_effect_for_stage_result(
        engine,
        request=SimpleNamespace(run_dir=str(run_dir)),
        stage_result=stage_result,
        router_decision=router_decision,
    )

    assert application.router_decision is router_decision
    assert application.source_lifecycle_applied is False
    assert stage_result.metadata["runtime_effect_handler_id"] == "planner_disposition"
    assert stage_result.metadata["runtime_effect_decision"] == "continue_route"


def test_planner_disposition_emitted_child_specs_resolves_incident_without_manager_route(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_incident(_incident_doc())
    assert queue.claim_next_planning_item() is not None
    queue.enqueue_spec(_spec_doc())
    engine = RuntimeEngine(paths, stage_runner=_unused_stage_runner, mode_id="blueprint_codex")
    engine.startup()
    run_dir = tmp_path / "run"
    _write_planner_disposition(
        run_dir,
        source_work_item_family_id="incident",
        source_work_item_id="incident-001",
        disposition="emitted_child_specs",
        emitted_spec_ids=("spec-child-001",),
    )
    stage_result = _planner_stage_result()
    router_decision = RouterDecision(
        action=RouterAction.RUN_STAGE,
        next_plane=stage_result.plane,
        next_stage=PlanningStageName.MANAGER,
        next_node_id="manager_blueprint",
        next_stage_kind_id="manager_blueprint",
        reason="planner:PLANNER_COMPLETE",
    )

    application = apply_runtime_effect_for_stage_result(
        engine,
        request=SimpleNamespace(run_dir=str(run_dir)),
        stage_result=stage_result,
        router_decision=router_decision,
    )

    assert application.router_decision.action is RouterAction.IDLE
    assert application.router_decision.reason == "planner_disposition"
    assert application.source_lifecycle_applied is True
    assert (paths.incidents_resolved_dir / "incident-001.md").is_file()
    assert (paths.specs_queue_dir / "spec-child-001.md").is_file()
    assert stage_result.metadata["runtime_effect_decision"] == "request_complete_source"
    assert stage_result.metadata["runtime_effect_source_lifecycle_action"] == "complete"


def test_planner_disposition_missing_child_spec_blocks_source(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_incident(_incident_doc())
    assert queue.claim_next_planning_item() is not None
    engine = RuntimeEngine(paths, stage_runner=_unused_stage_runner, mode_id="blueprint_codex")
    engine.startup()
    run_dir = tmp_path / "run"
    _write_planner_disposition(
        run_dir,
        source_work_item_family_id="incident",
        source_work_item_id="incident-001",
        disposition="emitted_child_specs",
        emitted_spec_ids=("spec-missing-child",),
    )
    stage_result = _planner_stage_result()
    router_decision = RouterDecision(
        action=RouterAction.RUN_STAGE,
        next_plane=stage_result.plane,
        next_stage=PlanningStageName.MANAGER,
        next_node_id="manager_blueprint",
        next_stage_kind_id="manager_blueprint",
        reason="planner:PLANNER_COMPLETE",
    )

    application = apply_runtime_effect_for_stage_result(
        engine,
        request=SimpleNamespace(run_dir=str(run_dir)),
        stage_result=stage_result,
        router_decision=router_decision,
    )

    assert application.router_decision.action is RouterAction.BLOCKED
    assert application.router_decision.failure_class == "planner_disposition_child_spec_missing"
    assert (paths.incidents_blocked_dir / "incident-001.md").is_file()
    assert not (paths.incidents_resolved_dir / "incident-001.md").exists()
    assert stage_result.metadata["runtime_effect_failure_class"] == (
        "planner_disposition_child_spec_missing"
    )


def test_planner_disposition_missing_artifact_blocks_source_even_when_child_spec_exists(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_incident(_incident_doc())
    assert queue.claim_next_planning_item() is not None
    queue.enqueue_spec(_spec_doc())
    engine = RuntimeEngine(paths, stage_runner=_unused_stage_runner, mode_id="blueprint_codex")
    engine.startup()
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    stage_result = _planner_stage_result()
    router_decision = RouterDecision(
        action=RouterAction.RUN_STAGE,
        next_plane=stage_result.plane,
        next_stage=PlanningStageName.MANAGER,
        next_node_id="manager_blueprint",
        next_stage_kind_id="manager_blueprint",
        reason="planner:PLANNER_COMPLETE",
    )

    application = apply_runtime_effect_for_stage_result(
        engine,
        request=SimpleNamespace(run_dir=str(run_dir)),
        stage_result=stage_result,
        router_decision=router_decision,
    )

    assert application.router_decision.action is RouterAction.BLOCKED
    assert application.router_decision.failure_class == "planner_disposition_missing"
    assert application.source_lifecycle_applied is True
    assert (paths.incidents_blocked_dir / "incident-001.md").is_file()
    assert not (paths.incidents_resolved_dir / "incident-001.md").exists()
    assert (paths.specs_queue_dir / "spec-child-001.md").is_file()
    assert stage_result.metadata["runtime_effect_decision"] == "request_block_source"
    assert stage_result.metadata["runtime_effect_failure_class"] == "planner_disposition_missing"


@pytest.mark.parametrize(
    ("mode_id", "next_node_id", "next_stage_kind_id"),
    [
        (None, "mechanic", "mechanic"),
        ("blueprint_codex", "mechanic_blueprint", "mechanic_blueprint"),
    ],
)
def test_planner_disposition_blocked_preserves_existing_blocked_recovery_route(
    tmp_path: Path,
    mode_id: str | None,
    next_node_id: str,
    next_stage_kind_id: str,
) -> None:
    paths = _workspace(tmp_path)
    engine = RuntimeEngine(paths, stage_runner=_unused_stage_runner, mode_id=mode_id)
    engine.startup()
    run_dir = tmp_path / "run"
    _write_planner_disposition(
        run_dir,
        source_work_item_family_id="spec",
        source_work_item_id="spec-001",
        disposition="blocked",
    )
    stage_result = _planner_stage_result(
        work_item_family_id="spec",
        work_item_kind=WorkItemKind.SPEC,
        work_item_id="spec-001",
        terminal_result="BLOCKED",
        result_class=ResultClass.BLOCKED,
        success=False,
    )
    router_decision = RouterDecision(
        action=RouterAction.RUN_STAGE,
        next_plane=stage_result.plane,
        next_stage=PlanningStageName.MECHANIC,
        next_node_id=next_node_id,
        next_stage_kind_id=next_stage_kind_id,
        reason="planner:BLOCKED",
    )

    application = apply_runtime_effect_for_stage_result(
        engine,
        request=SimpleNamespace(run_dir=str(run_dir)),
        stage_result=stage_result,
        router_decision=router_decision,
    )

    assert application.router_decision is router_decision
    assert application.source_lifecycle_applied is False
    assert stage_result.metadata["runtime_effect_handler_id"] == "planner_disposition"
    assert stage_result.metadata["runtime_effect_decision"] == "continue_route"


def test_pre_mutation_effect_failure_routes_through_runtime_failure_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _workspace(tmp_path)
    engine = RuntimeEngine(paths, stage_runner=_unused_stage_runner, mode_id="blueprint_codex")
    engine.startup()
    assert engine.compiled_plan is not None
    run_dir = tmp_path / "run"
    stage_result_path = run_dir / "stage_results" / "request-001.json"
    stage_result_path.parent.mkdir(parents=True, exist_ok=True)

    def _handler(paths, stage_result, run_dir, compiled_plan):
        return RuntimeEffectResult(
            handler_id="evaluator_blueprint_approved_to_task",
            decision=RuntimeEffectDecision.REQUEST_BLOCK_SOURCE,
            mutation_phase=RuntimeEffectMutationPhase.PRE_MUTATION,
            failure_class="generated_task_missing",
            message="generated_task.json is missing",
            source_lifecycle_intent=SourceLifecycleIntent(
                lifecycle_plan_id="block_blueprint_draft_after_effect",
                action=SourceLifecycleAction.BLOCK,
                work_item_kind=WorkItemKind.BLUEPRINT_DRAFT,
                work_item_id="draft-blueprint-001",
            ),
        )

    monkeypatch.setitem(
        effect_execution._HANDLERS_BY_ID,
        "evaluator_blueprint_approved_to_task",
        _handler,
    )
    stage_result = _blueprint_approval_stage_result()
    router_decision = RouterDecision(
        action=RouterAction.IDLE,
        next_plane=None,
        next_stage=None,
        reason="blueprint_approved",
    )

    application = apply_runtime_effect_for_stage_result(
        engine,
        request=SimpleNamespace(run_dir=str(run_dir)),
        stage_result=stage_result,
        router_decision=router_decision,
        stage_result_path=stage_result_path,
    )

    assert application.router_decision.action is RouterAction.RUN_STAGE
    assert application.router_decision.next_stage is PlanningStageName.MECHANIC
    assert application.router_decision.next_node_id == "mechanic_blueprint"
    assert application.router_decision.next_stage_kind_id == "mechanic_blueprint"
    assert application.router_decision.failure_class == "generated_task_missing"
    assert application.router_decision.reason == (
        "runtime_effect_failure:evaluator_blueprint_approved_to_task:generated_task_missing"
    )
    assert application.source_lifecycle_applied is False
    persisted = StageResultEnvelope.model_validate_json(
        stage_result_path.read_text(encoding="utf-8")
    )
    assert persisted.metadata["runtime_effect_handler_id"] == (
        "evaluator_blueprint_approved_to_task"
    )
    assert persisted.metadata["runtime_effect_decision"] == "request_block_source"
    assert persisted.metadata["runtime_effect_failure_class"] == "generated_task_missing"
    assert persisted.metadata["runtime_effect_failure_message"] == (
        "generated_task.json is missing"
    )
    assert persisted.metadata["runtime_effect_mutation_phase"] == "pre_mutation"


def test_manager_blueprint_malformed_artifact_routes_through_runtime_failure_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _workspace(tmp_path)
    engine = RuntimeEngine(paths, stage_runner=_unused_stage_runner, mode_id="blueprint_codex")
    engine.startup()
    assert engine.compiled_plan is not None
    run_dir = tmp_path / "run"
    stage_result_path = run_dir / "stage_results" / "request-001.json"
    stage_result_path.parent.mkdir(parents=True, exist_ok=True)

    def _handler(paths, stage_result, run_dir, compiled_plan):
        return RuntimeEffectResult(
            handler_id="manager_blueprint_manifest_to_blueprint_drafts",
            decision=RuntimeEffectDecision.REQUEST_BLOCK_SOURCE,
            mutation_phase=RuntimeEffectMutationPhase.PRE_MUTATION,
            failure_class="blueprint_manifest_parse_error",
            message="blueprint_manifest.json is malformed",
        )

    monkeypatch.setitem(
        effect_execution._HANDLERS_BY_ID,
        "manager_blueprint_manifest_to_blueprint_drafts",
        _handler,
    )
    stage_result = _manager_blueprint_stage_result()
    router_decision = RouterDecision(
        action=RouterAction.IDLE,
        next_plane=None,
        next_stage=None,
        reason="manager_blueprint_complete",
    )

    application = apply_runtime_effect_for_stage_result(
        engine,
        request=SimpleNamespace(run_dir=str(run_dir)),
        stage_result=stage_result,
        router_decision=router_decision,
        stage_result_path=stage_result_path,
    )

    assert application.router_decision.action is RouterAction.BLOCKED
    assert application.router_decision.next_node_id is None
    assert application.router_decision.failure_class == "blueprint_manifest_parse_error"
    persisted = StageResultEnvelope.model_validate_json(
        stage_result_path.read_text(encoding="utf-8")
    )
    assert persisted.metadata["runtime_effect_failure_policy_id"] == (
        "manager_blueprint_pre_mutation_artifact_repair"
    )
    assert persisted.metadata["runtime_effect_recovery_action"] == "block_source_work_item"
    assert persisted.metadata["runtime_effect_failure_class"] == (
        "blueprint_manifest_parse_error"
    )


@pytest.mark.parametrize(
    "failure_class",
    [
        "blueprint_manifest_duplicate",
        "blueprint_source_lifecycle_invalid",
    ],
)
def test_manager_blueprint_conservative_failure_policy_blocks_and_annotates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_class: str,
) -> None:
    paths = _workspace(tmp_path)
    engine = RuntimeEngine(paths, stage_runner=_unused_stage_runner, mode_id="blueprint_codex")
    engine.startup()
    assert engine.compiled_plan is not None
    run_dir = tmp_path / "run"
    stage_result_path = run_dir / "stage_results" / "request-001.json"
    stage_result_path.parent.mkdir(parents=True, exist_ok=True)

    def _handler(paths, stage_result, run_dir, compiled_plan):
        return RuntimeEffectResult(
            handler_id="manager_blueprint_manifest_to_blueprint_drafts",
            decision=RuntimeEffectDecision.REQUEST_BLOCK_SOURCE,
            mutation_phase=RuntimeEffectMutationPhase.PRE_MUTATION,
            failure_class=failure_class,
            message=f"{failure_class}: conservative source block",
        )

    monkeypatch.setitem(
        effect_execution._HANDLERS_BY_ID,
        "manager_blueprint_manifest_to_blueprint_drafts",
        _handler,
    )
    stage_result = _manager_blueprint_stage_result()
    router_decision = RouterDecision(
        action=RouterAction.IDLE,
        next_plane=None,
        next_stage=None,
        reason="manager_blueprint_complete",
    )

    application = apply_runtime_effect_for_stage_result(
        engine,
        request=SimpleNamespace(run_dir=str(run_dir)),
        stage_result=stage_result,
        router_decision=router_decision,
        stage_result_path=stage_result_path,
    )

    assert application.router_decision.action is RouterAction.BLOCKED
    assert application.router_decision.next_node_id is None
    assert application.router_decision.failure_class == failure_class
    persisted = StageResultEnvelope.model_validate_json(
        stage_result_path.read_text(encoding="utf-8")
    )
    assert persisted.metadata["runtime_effect_failure_policy_id"] == (
        "manager_blueprint_pre_mutation_conservative_block"
    )
    assert persisted.metadata["runtime_effect_recovery_action"] == "block_source_work_item"
    assert persisted.metadata["runtime_effect_failure_class"] == failure_class


def test_manager_blueprint_partial_mutation_policy_blocks_without_model_repair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _workspace(tmp_path)
    engine = RuntimeEngine(paths, stage_runner=_unused_stage_runner, mode_id="blueprint_codex")
    engine.startup()
    assert engine.compiled_plan is not None
    created_path = paths.runtime_root / "blueprints" / "manifests" / "manifest-partial.json"
    created_path.parent.mkdir(parents=True, exist_ok=True)
    created_path.write_text("{}\n", encoding="utf-8")
    run_dir = tmp_path / "run"
    stage_result_path = run_dir / "stage_results" / "request-001.json"
    stage_result_path.parent.mkdir(parents=True, exist_ok=True)

    def _handler(paths, stage_result, run_dir, compiled_plan):
        return RuntimeEffectResult(
            handler_id="manager_blueprint_manifest_to_blueprint_drafts",
            decision=RuntimeEffectDecision.REQUEST_BLOCK_SOURCE,
            mutation_phase=RuntimeEffectMutationPhase.PARTIAL_MUTATION,
            created_paths=(str(created_path.relative_to(paths.root)),),
            failure_class="blueprint_partial_mutation",
            message="failed after writing the manifest",
        )

    monkeypatch.setitem(
        effect_execution._HANDLERS_BY_ID,
        "manager_blueprint_manifest_to_blueprint_drafts",
        _handler,
    )
    stage_result = _manager_blueprint_stage_result()
    router_decision = RouterDecision(
        action=RouterAction.IDLE,
        next_plane=None,
        next_stage=None,
        reason="manager_blueprint_complete",
    )

    application = apply_runtime_effect_for_stage_result(
        engine,
        request=SimpleNamespace(run_dir=str(run_dir)),
        stage_result=stage_result,
        router_decision=router_decision,
        stage_result_path=stage_result_path,
    )

    assert application.router_decision.action is RouterAction.BLOCKED
    assert application.router_decision.next_node_id is None
    assert application.router_decision.failure_class == "blueprint_partial_mutation"
    persisted = StageResultEnvelope.model_validate_json(
        stage_result_path.read_text(encoding="utf-8")
    )
    assert persisted.metadata["runtime_effect_failure_policy_id"] == (
        "manager_blueprint_partial_mutation_conservative_block"
    )
    assert persisted.metadata["runtime_effect_recovery_action"] == "block_source_work_item"
    assert persisted.metadata["runtime_effect_mutation_phase"] == "partial_mutation"


def test_runtime_effect_require_operator_policy_blocks_with_operator_visible_reason_and_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _workspace(tmp_path)
    engine = RuntimeEngine(paths, stage_runner=_unused_stage_runner, mode_id="blueprint_codex")
    engine.startup()
    assert engine.compiled_plan is not None
    require_operator_policy = _runtime_failure_policy()
    engine.compiled_plan = engine.compiled_plan.model_copy(
        update={
            "runtime_failure_policies_by_id": {
                require_operator_policy.policy_id: require_operator_policy,
                **engine.compiled_plan.runtime_failure_policies_by_id,
            }
        }
    )
    run_dir = tmp_path / "run"
    stage_result_path = run_dir / "stage_results" / "request-001.json"
    stage_result_path.parent.mkdir(parents=True, exist_ok=True)

    def _handler(paths, stage_result, run_dir, compiled_plan):
        return RuntimeEffectResult(
            handler_id="manager_blueprint_manifest_to_blueprint_drafts",
            decision=RuntimeEffectDecision.REQUEST_BLOCK_SOURCE,
            mutation_phase=RuntimeEffectMutationPhase.PRE_MUTATION,
            failure_class="blueprint_manifest_duplicate",
            message="duplicate manifest requires operator review",
        )

    monkeypatch.setitem(
        effect_execution._HANDLERS_BY_ID,
        "manager_blueprint_manifest_to_blueprint_drafts",
        _handler,
    )
    stage_result = _manager_blueprint_stage_result()
    router_decision = RouterDecision(
        action=RouterAction.IDLE,
        next_plane=None,
        next_stage=None,
        reason="manager_blueprint_complete",
    )

    application = apply_runtime_effect_for_stage_result(
        engine,
        request=SimpleNamespace(run_dir=str(run_dir)),
        stage_result=stage_result,
        router_decision=router_decision,
        stage_result_path=stage_result_path,
    )

    assert application.router_decision.action is RouterAction.BLOCKED
    assert application.router_decision.reason == (
        "runtime_effect_requires_operator:"
        "manager_blueprint_manifest_to_blueprint_drafts:"
        "blueprint_manifest_duplicate"
    )
    assert application.router_decision.failure_class == "blueprint_manifest_duplicate"
    persisted = StageResultEnvelope.model_validate_json(
        stage_result_path.read_text(encoding="utf-8")
    )
    assert persisted.metadata["runtime_effect_failure_policy_id"] == (
        "test_require_operator_policy"
    )
    assert persisted.metadata["runtime_effect_recovery_action"] == "require_operator"
    events = [
        event for event in read_runtime_events(paths)
        if event.event_type == "runtime_effect_applied"
    ]
    assert events[-1].data["failure_policy_id"] == "test_require_operator_policy"
    assert events[-1].data["failure_policy_action"] == "require_operator"


def test_partial_mutation_effect_failure_blocks_source_conservatively(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _workspace(tmp_path)
    engine = RuntimeEngine(paths, stage_runner=_unused_stage_runner, mode_id="blueprint_codex")
    engine.startup()
    assert engine.compiled_plan is not None
    created_path = (
        paths.runtime_root
        / "blueprints"
        / "evaluations"
        / "evaluation-blueprint-001.json"
    )
    created_path.parent.mkdir(parents=True, exist_ok=True)
    created_path.write_text("{}\n", encoding="utf-8")

    def _handler(paths, stage_result, run_dir, compiled_plan):
        return RuntimeEffectResult(
            handler_id="evaluator_blueprint_approved_to_task",
            decision=RuntimeEffectDecision.REQUEST_BLOCK_SOURCE,
            mutation_phase=RuntimeEffectMutationPhase.PARTIAL_MUTATION,
            created_paths=(str(created_path.relative_to(paths.root)),),
            failure_class="generated_task_missing",
            message="failed after creating an evaluation record",
        )

    monkeypatch.setitem(
        effect_execution._HANDLERS_BY_ID,
        "evaluator_blueprint_approved_to_task",
        _handler,
    )
    stage_result = _blueprint_approval_stage_result()
    router_decision = RouterDecision(
        action=RouterAction.IDLE,
        next_plane=None,
        next_stage=None,
        reason="blueprint_approved",
    )

    application = apply_runtime_effect_for_stage_result(
        engine,
        request=SimpleNamespace(run_dir=str(tmp_path / "run")),
        stage_result=stage_result,
        router_decision=router_decision,
    )

    assert application.router_decision.action is RouterAction.BLOCKED
    assert application.router_decision.failure_class == "generated_task_missing"
    assert stage_result.metadata["runtime_effect_mutation_phase"] == "partial_mutation"
    assert stage_result.metadata["runtime_effect_failure_class"] == "generated_task_missing"


def test_effect_failure_with_created_paths_cannot_route_as_pre_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _workspace(tmp_path)
    engine = RuntimeEngine(paths, stage_runner=_unused_stage_runner, mode_id="blueprint_codex")
    engine.startup()
    assert engine.compiled_plan is not None
    created_path = (
        paths.runtime_root
        / "blueprints"
        / "evaluations"
        / "evaluation-blueprint-002.json"
    )
    created_path.parent.mkdir(parents=True, exist_ok=True)
    created_path.write_text("{}\n", encoding="utf-8")

    def _handler(paths, stage_result, run_dir, compiled_plan):
        return RuntimeEffectResult(
            handler_id="evaluator_blueprint_approved_to_task",
            decision=RuntimeEffectDecision.REQUEST_BLOCK_SOURCE,
            mutation_phase=RuntimeEffectMutationPhase.PRE_MUTATION,
            created_paths=(str(created_path.relative_to(paths.root)),),
            failure_class="generated_task_missing",
            message="handler misclassified a partial mutation",
        )

    monkeypatch.setitem(
        effect_execution._HANDLERS_BY_ID,
        "evaluator_blueprint_approved_to_task",
        _handler,
    )
    stage_result = _blueprint_approval_stage_result()
    router_decision = RouterDecision(
        action=RouterAction.IDLE,
        next_plane=None,
        next_stage=None,
        reason="blueprint_approved",
    )

    application = apply_runtime_effect_for_stage_result(
        engine,
        request=SimpleNamespace(run_dir=str(tmp_path / "run")),
        stage_result=stage_result,
        router_decision=router_decision,
    )

    assert application.router_decision.action is RouterAction.BLOCKED
    assert application.router_decision.next_node_id is None
    assert application.router_decision.failure_class == "generated_task_missing"
    assert stage_result.metadata["runtime_effect_mutation_phase"] == "partial_mutation"
    assert stage_result.metadata["runtime_effect_failure_class"] == "generated_task_missing"


def test_stage_effect_application_reports_only_destination_queue_paths_as_spawned_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _workspace(tmp_path)
    engine = RuntimeEngine(paths, stage_runner=_unused_stage_runner)
    engine.startup()
    queue_path = paths.tasks_queue_dir / "task-002.md"
    queue_path.write_text("queued task artifact", encoding="utf-8")
    side_path = paths.root / "millrace-agents" / "blueprints" / "promotions" / "promotion-001.json"
    side_path.parent.mkdir(parents=True, exist_ok=True)
    side_path.write_text("{}", encoding="utf-8")

    def _handler(paths, stage_result, run_dir, compiled_plan):
        return RuntimeEffectResult(
            handler_id="custom_task_promotion",
            decision=RuntimeEffectDecision.CONTINUE_ROUTE,
            created_paths=(
                str(queue_path.relative_to(paths.root)),
                str(side_path.relative_to(paths.root)),
            ),
        )

    monkeypatch.setitem(
        effect_execution._HANDLERS_BY_ID,
        "custom_task_promotion",
        _handler,
    )
    compiled_plan = SimpleNamespace(
        runtime_effect_rules=(
            SimpleNamespace(
                rule_id="custom-task-promotion",
                source_node_id="builder",
                on_outcomes=("BUILDER_COMPLETE",),
                handler_id="custom_task_promotion",
                destination_family_id="task",
            ),
        ),
        work_item_families_by_id={
            "task": SimpleNamespace(
                queue_dirs=SimpleNamespace(queue="millrace-agents/tasks/queue")
            )
        },
    )
    stage_result = _stage_result()
    router_decision = RouterDecision(
        action=RouterAction.IDLE,
        next_plane=None,
        next_stage=None,
        reason="test_route",
    )

    application = apply_runtime_effect_for_stage_result(
        engine,
        request=SimpleNamespace(run_dir=str(tmp_path / "run")),
        stage_result=stage_result,
        router_decision=router_decision,
        compiled_plan=compiled_plan,
    )

    assert application.router_decision is router_decision
    assert application.spawned_paths == (queue_path,)
    assert set(stage_result.artifact_paths) == {
        str(queue_path.relative_to(paths.root)),
        str(side_path.relative_to(paths.root)),
    }
