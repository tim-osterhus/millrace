from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from millrace_ai.architecture import RuntimeEffectOperationRunnerDefinition
from millrace_ai.architecture.workflow_primitives import (
    builtin_queue_lifecycle_adapter_id_for_family,
)
from millrace_ai.contracts import (
    BlueprintDraftDocument,
    IncidentDocument,
    Plane,
    PlanningStageName,
    RecoveryCounterEntry,
    RecoveryCounters,
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
from millrace_ai.runtime.effects.interpreter import (
    INTERPRETED_RUNNER_ID,
    interpret_operation,
)
from millrace_ai.runtime.effects.journal import (
    completed_hashes_by_step,
    compute_idempotency_hash,
    has_started_record,
    read_journal_records,
    write_completed_record,
    write_started_record,
)
from millrace_ai.runtime.engine import RuntimeEngine
from millrace_ai.state_store import load_recovery_counters, save_recovery_counters

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


def _activate_blueprint_draft(paths, draft_id: str = "draft-blueprint-001") -> None:
    active_dir = paths.runtime_root / "blueprints/drafts/active"
    active_dir.mkdir(parents=True, exist_ok=True)
    draft = BlueprintDraftDocument(
        draft_id=draft_id,
        manifest_id="manifest-001",
        root_spec_id="spec-root-001",
        root_idea_id="idea-001",
        source_spec_id="spec-root-001",
        draft_index=1,
        title="Blueprint draft",
        summary="Draft for runtime effect tests.",
        target_paths=("src/millrace_ai/runtime/",),
        acceptance_intent=("Runtime effect tests pass.",),
        context_excerpt="Test context.",
        current_revision=0,
        status="active",
        created_at=NOW,
    )
    (active_dir / f"{draft_id}.json").write_text(
        draft.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
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


def test_runtime_effect_result_contract_accepts_operation_only_identity() -> None:
    result = RuntimeEffectResult(
        operation_id="generated_task_artifact_to_task_queue",
        runner_id="custom_effect_runner",
        decision=RuntimeEffectDecision.CONTINUE_ROUTE,
    )

    assert result.handler_id is None
    assert result.operation_id == "generated_task_artifact_to_task_queue"
    assert result.runner_id == "custom_effect_runner"


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


def test_effect_applies_source_lifecycle_after_destination_artifacts_exist(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from millrace_ai.workspace import family_adapters

    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_task(_task_doc())
    assert queue.claim_next_execution_task() is not None
    created_path = paths.tasks_queue_dir / "task-002.md"
    created_path.write_text("queued task artifact", encoding="utf-8")
    called_adapter_ids: list[str] = []
    real_queue_adapter_for_id = family_adapters.queue_adapter_for_id

    def capture_queue_adapter(adapter_id: str):
        called_adapter_ids.append(adapter_id)
        return real_queue_adapter_for_id(adapter_id)

    monkeypatch.setattr(family_adapters, "queue_adapter_for_id", capture_queue_adapter)

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
    task_adapter_id = builtin_queue_lifecycle_adapter_id_for_family("task")
    assert task_adapter_id is not None
    assert task_adapter_id in called_adapter_ids


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
        ("blueprint_" "codex", "manager_blueprint", "manager_blueprint"),
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
    assert stage_result.metadata["runtime_effect_operation_id"] == "planner_disposition"
    assert stage_result.metadata["runtime_effect_runner_id"] == "legacy_python_handler"
    assert stage_result.metadata["runtime_effect_legacy_handler_id"] == "planner_disposition"
    assert stage_result.metadata["runtime_effect_decision"] == "continue_route"


def test_planner_disposition_emitted_child_specs_resolves_incident_without_manager_route(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_incident(_incident_doc())
    assert queue.claim_next_planning_item() is not None
    queue.enqueue_spec(_spec_doc())
    engine = RuntimeEngine(paths, stage_runner=_unused_stage_runner, mode_id="blueprint_" "codex")
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
    engine = RuntimeEngine(paths, stage_runner=_unused_stage_runner, mode_id="blueprint_" "codex")
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

    assert application.router_decision.action is RouterAction.RUN_STAGE
    assert application.router_decision.next_stage is PlanningStageName.MECHANIC
    assert application.router_decision.next_node_id == "mechanic_blueprint"
    assert application.router_decision.next_stage_kind_id == "mechanic_blueprint"
    assert application.router_decision.failure_class == "planner_disposition_child_spec_missing"
    assert application.source_lifecycle_applied is False
    assert not (paths.incidents_blocked_dir / "incident-001.md").exists()
    assert not (paths.incidents_resolved_dir / "incident-001.md").exists()
    assert stage_result.metadata["runtime_effect_failure_class"] == (
        "planner_disposition_child_spec_missing"
    )
    assert stage_result.metadata["runtime_effect_recovery_action"] == "default_runtime_repair"


def test_planner_disposition_missing_artifact_blocks_source_even_when_child_spec_exists(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_incident(_incident_doc())
    assert queue.claim_next_planning_item() is not None
    queue.enqueue_spec(_spec_doc())
    engine = RuntimeEngine(paths, stage_runner=_unused_stage_runner, mode_id="blueprint_" "codex")
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

    assert application.router_decision.action is RouterAction.RUN_STAGE
    assert application.router_decision.next_stage is PlanningStageName.MECHANIC
    assert application.router_decision.next_node_id == "mechanic_blueprint"
    assert application.router_decision.next_stage_kind_id == "mechanic_blueprint"
    assert application.router_decision.failure_class == "planner_disposition_missing"
    assert application.source_lifecycle_applied is False
    assert not (paths.incidents_blocked_dir / "incident-001.md").exists()
    assert not (paths.incidents_resolved_dir / "incident-001.md").exists()
    assert (paths.specs_queue_dir / "spec-child-001.md").is_file()
    assert stage_result.metadata["runtime_effect_decision"] == "request_block_source"
    assert stage_result.metadata["runtime_effect_failure_class"] == "planner_disposition_missing"
    assert stage_result.metadata["runtime_effect_recovery_action"] == "default_runtime_repair"


def test_final_planner_block_uses_rule_declared_source_blocking_lifecycle_plan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_incident(_incident_doc())
    assert queue.claim_next_planning_item() is not None
    engine = RuntimeEngine(paths, stage_runner=_unused_stage_runner, mode_id="blueprint_" "codex")
    engine.startup()
    run_dir = tmp_path / "run"
    stage_result_path = run_dir / "stage_results" / "request-001.json"
    stage_result_path.parent.mkdir(parents=True, exist_ok=True)

    def _handler(paths, stage_result, run_dir, compiled_plan):
        return RuntimeEffectResult(
            handler_id="planner_disposition",
            decision=RuntimeEffectDecision.REQUEST_BLOCK_SOURCE,
            mutation_phase=RuntimeEffectMutationPhase.PARTIAL_MUTATION,
            failure_class="planner_terminal_block",
            message="planner produced a final block outcome",
        )

    monkeypatch.setitem(effect_execution._HANDLERS_BY_ID, "planner_disposition", _handler)
    stage_result = _planner_stage_result()

    application = apply_runtime_effect_for_stage_result(
        engine,
        request=SimpleNamespace(run_dir=str(run_dir)),
        stage_result=stage_result,
        router_decision=RouterDecision(
            action=RouterAction.IDLE,
            next_plane=None,
            next_stage=None,
            reason="planner_complete",
        ),
        stage_result_path=stage_result_path,
    )

    assert application.router_decision.action is RouterAction.BLOCKED
    assert application.router_decision.failure_class == "planner_terminal_block"
    assert application.source_lifecycle_applied is True
    assert (paths.incidents_blocked_dir / "incident-001.md").is_file()
    persisted = StageResultEnvelope.model_validate_json(
        stage_result_path.read_text(encoding="utf-8")
    )
    assert persisted.metadata["runtime_effect_source_lifecycle_plan_id"] == (
        "block_source_after_effect"
    )
    assert persisted.metadata["runtime_effect_source_lifecycle_action"] == "block"


@pytest.mark.parametrize(
    ("mode_id", "next_node_id", "next_stage_kind_id"),
    [
        (None, "mechanic", "mechanic"),
        ("blueprint_" "codex", "mechanic_blueprint", "mechanic_blueprint"),
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
    engine = RuntimeEngine(paths, stage_runner=_unused_stage_runner, mode_id="blueprint_" "codex")
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
    assert persisted.metadata["runtime_effect_operation_id"] == (
        "evaluator_blueprint_approved_to_task"
    )
    assert persisted.metadata["runtime_effect_runner_id"] == "legacy_python_handler"
    assert persisted.metadata["runtime_effect_legacy_handler_id"] == (
        "evaluator_blueprint_approved_to_task"
    )
    assert persisted.metadata["runtime_effect_decision"] == "request_block_source"
    assert persisted.metadata["runtime_effect_failure_class"] == "generated_task_missing"
    assert persisted.metadata["runtime_effect_failure_message"] == (
        "generated_task.json is missing"
    )
    assert persisted.metadata["runtime_effect_mutation_phase"] == "pre_mutation"
    assert persisted.metadata["runtime_effect_source_lifecycle_plan_id"] is None
    assert persisted.metadata["runtime_effect_source_lifecycle_action"] is None


def test_manager_blueprint_malformed_artifact_routes_through_runtime_failure_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_spec(_spec_doc("spec-blueprint-001"))
    assert queue.claim_next_planning_item() is not None
    engine = RuntimeEngine(paths, stage_runner=_unused_stage_runner, mode_id="blueprint_" "codex")
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
    assert persisted.metadata["runtime_effect_source_lifecycle_plan_id"] == (
        "block_spec_source_after_blueprint_effect"
    )
    assert persisted.metadata["runtime_effect_source_lifecycle_action"] == "block"
    assert (paths.specs_blocked_dir / "spec-blueprint-001.md").is_file()


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
    queue = QueueStore(paths)
    queue.enqueue_spec(_spec_doc("spec-blueprint-001"))
    assert queue.claim_next_planning_item() is not None
    engine = RuntimeEngine(paths, stage_runner=_unused_stage_runner, mode_id="blueprint_" "codex")
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
    assert persisted.metadata["runtime_effect_source_lifecycle_plan_id"] == (
        "block_spec_source_after_blueprint_effect"
    )
    assert persisted.metadata["runtime_effect_source_lifecycle_action"] == "block"
    assert (paths.specs_blocked_dir / "spec-blueprint-001.md").is_file()


def test_unclassified_pre_mutation_effect_failure_routes_to_default_repair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _workspace(tmp_path)
    engine = RuntimeEngine(paths, stage_runner=_unused_stage_runner, mode_id="blueprint_" "codex")
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
            failure_class="unclassified_manifest_handoff",
            message="manifest handoff was diagnosable but unclassified",
        )

    monkeypatch.setitem(
        effect_execution._HANDLERS_BY_ID,
        "manager_blueprint_manifest_to_blueprint_drafts",
        _handler,
    )
    stage_result = _manager_blueprint_stage_result()

    application = apply_runtime_effect_for_stage_result(
        engine,
        request=SimpleNamespace(run_dir=str(run_dir)),
        stage_result=stage_result,
        router_decision=RouterDecision(
            action=RouterAction.IDLE,
            next_plane=None,
            next_stage=None,
            reason="manager_blueprint_complete",
        ),
        stage_result_path=stage_result_path,
    )

    assert application.router_decision.action is RouterAction.RUN_STAGE
    assert application.router_decision.next_stage is PlanningStageName.MECHANIC
    assert application.router_decision.next_node_id == "mechanic_blueprint"
    assert application.router_decision.next_stage_kind_id == "mechanic_blueprint"
    assert application.router_decision.failure_class == "unclassified_manifest_handoff"
    persisted = StageResultEnvelope.model_validate_json(
        stage_result_path.read_text(encoding="utf-8")
    )
    assert persisted.metadata["runtime_effect_recovery_action"] == "default_runtime_repair"
    assert persisted.metadata["runtime_effect_failure_class"] == "unclassified_manifest_handoff"
    assert persisted.metadata["runtime_effect_source_lifecycle_plan_id"] is None
    assert persisted.metadata["runtime_effect_source_lifecycle_action"] is None
    context = json.loads(paths.runtime_error_context_file.read_text(encoding="utf-8"))
    assert context["error_code"] == "planning_post_stage_apply_failed"
    assert context["repair_stage"] == "mechanic"
    assert context["exception_message"].startswith(
        "manager_blueprint_manifest_to_blueprint_drafts:unclassified_manifest_handoff"
    )
    assert Path(context["report_path"]).is_file()


def test_default_runtime_effect_repair_blocks_after_threshold(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_spec(_spec_doc("spec-blueprint-001"))
    assert queue.claim_next_planning_item() is not None
    engine = RuntimeEngine(paths, stage_runner=_unused_stage_runner, mode_id="blueprint_" "codex")
    engine.startup()
    save_recovery_counters(
        paths,
        RecoveryCounters(
            entries=(
                RecoveryCounterEntry(
                    failure_class="unclassified_manifest_handoff",
                    work_item_family_id="spec",
                    work_item_kind=WorkItemKind.SPEC,
                    work_item_id="spec-blueprint-001",
                    counters={"mechanic_attempt_count": 2},
                    last_updated_at=NOW,
                ),
            )
        ),
    )
    engine.counters = load_recovery_counters(paths)
    assert engine.snapshot is not None
    engine.snapshot = engine.snapshot.model_copy(
        update={
            "active_plane": Plane.PLANNING,
            "active_stage": PlanningStageName.MANAGER,
            "active_work_item_family_id": "spec",
            "active_work_item_kind": WorkItemKind.SPEC,
            "active_work_item_id": "spec-blueprint-001",
            "current_failure_class": "unclassified_manifest_handoff",
        }
    )
    run_dir = tmp_path / "run"

    def _handler(paths, stage_result, run_dir, compiled_plan):
        return RuntimeEffectResult(
            handler_id="manager_blueprint_manifest_to_blueprint_drafts",
            decision=RuntimeEffectDecision.REQUEST_BLOCK_SOURCE,
            mutation_phase=RuntimeEffectMutationPhase.PRE_MUTATION,
            failure_class="unclassified_manifest_handoff",
            message="manifest handoff was diagnosable but unclassified",
        )

    monkeypatch.setitem(
        effect_execution._HANDLERS_BY_ID,
        "manager_blueprint_manifest_to_blueprint_drafts",
        _handler,
    )

    application = apply_runtime_effect_for_stage_result(
        engine,
        request=SimpleNamespace(run_dir=str(run_dir)),
        stage_result=_manager_blueprint_stage_result(),
        router_decision=RouterDecision(
            action=RouterAction.IDLE,
            next_plane=None,
            next_stage=None,
            reason="manager_blueprint_complete",
        ),
    )

    assert application.router_decision.action is RouterAction.BLOCKED
    assert application.router_decision.reason == (
        "runtime_effect_failure:manager_blueprint_manifest_to_blueprint_drafts:"
        "unclassified_manifest_handoff:repair_attempts_exhausted"
    )
    assert application.router_decision.failure_class == "unclassified_manifest_handoff"
    assert application.router_decision.terminal_state_id == "blocked"
    assert application.router_decision.terminal_action_id == "block_work_item"
    assert application.router_decision.terminal_action_router_consequence == "blocked"
    assert application.router_decision.lifecycle_mutation_plan_id == "block_work_item"
    assert application.router_decision.lifecycle_action_id == "block"
    assert application.source_lifecycle_applied is True
    assert (paths.specs_blocked_dir / "spec-blueprint-001.md").is_file()


def test_manager_blueprint_partial_mutation_policy_blocks_without_model_repair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_spec(_spec_doc("spec-blueprint-001"))
    assert queue.claim_next_planning_item() is not None
    engine = RuntimeEngine(paths, stage_runner=_unused_stage_runner, mode_id="blueprint_" "codex")
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
    assert persisted.metadata["runtime_effect_source_lifecycle_plan_id"] == (
        "block_spec_source_after_blueprint_effect"
    )
    assert persisted.metadata["runtime_effect_source_lifecycle_action"] == "block"
    assert (paths.specs_blocked_dir / "spec-blueprint-001.md").is_file()


def test_runtime_effect_require_operator_policy_blocks_with_operator_visible_reason_and_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _workspace(tmp_path)
    queue = QueueStore(paths)
    queue.enqueue_spec(_spec_doc("spec-blueprint-001"))
    assert queue.claim_next_planning_item() is not None
    engine = RuntimeEngine(paths, stage_runner=_unused_stage_runner, mode_id="blueprint_" "codex")
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
    assert persisted.metadata["runtime_effect_source_lifecycle_plan_id"] == (
        "block_spec_source_after_blueprint_effect"
    )
    assert persisted.metadata["runtime_effect_source_lifecycle_action"] == "block"
    assert (paths.specs_blocked_dir / "spec-blueprint-001.md").is_file()
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
    _activate_blueprint_draft(paths)
    engine = RuntimeEngine(paths, stage_runner=_unused_stage_runner, mode_id="blueprint_" "codex")
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
    assert stage_result.metadata["runtime_effect_source_lifecycle_plan_id"] == (
        "block_blueprint_draft_after_effect"
    )
    assert stage_result.metadata["runtime_effect_source_lifecycle_action"] == "block"
    assert (
        paths.runtime_root / "blueprints/drafts/blocked/draft-blueprint-001.json"
    ).is_file()


def test_effect_failure_with_created_paths_cannot_route_as_pre_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _workspace(tmp_path)
    _activate_blueprint_draft(paths)
    engine = RuntimeEngine(paths, stage_runner=_unused_stage_runner, mode_id="blueprint_" "codex")
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
    assert stage_result.metadata["runtime_effect_source_lifecycle_plan_id"] == (
        "block_blueprint_draft_after_effect"
    )
    assert stage_result.metadata["runtime_effect_source_lifecycle_action"] == "block"
    assert (
        paths.runtime_root / "blueprints/drafts/blocked/draft-blueprint-001.json"
    ).is_file()


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
                effect_operation_id="custom_task_promotion",
                source_node_id="builder",
                on_outcomes=("BUILDER_COMPLETE",),
                handler_id=None,
                destination_family_id="task",
            ),
        ),
        runtime_effect_runners_by_id={
            "custom_effect_runner": RuntimeEffectOperationRunnerDefinition(
                runner_id="custom_effect_runner",
                operation_ids=("custom_task_promotion",),
            )
        },
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


# ---------------------------------------------------------------------------
# Runtime effect journal unit tests
# ---------------------------------------------------------------------------


def test_journal_write_started_and_read_records(tmp_path: Path) -> None:
    journal_dir = tmp_path / "journal"
    write_started_record(
        journal_dir,
        operation_id="op-001",
        step_id="step-a",
        params={"store_id": "s1", "mode": "test"},
        reads_artifact_ids=("artifact-1",),
    )
    records = read_journal_records(journal_dir, "op-001")
    assert len(records) == 1
    assert records[0]["record_type"] == "started"
    assert records[0]["operation_id"] == "op-001"
    assert records[0]["step_id"] == "step-a"
    assert records[0]["params"] == {"mode": "test", "store_id": "s1"}
    assert records[0]["reads_artifact_ids"] == ["artifact-1"]


def test_journal_write_completed_and_read_records(tmp_path: Path) -> None:
    journal_dir = tmp_path / "journal"
    idem = write_completed_record(
        journal_dir,
        operation_id="op-001",
        step_id="step-a",
        params={"store_id": "s1"},
        reads_artifact_ids=("artifact-1",),
    )
    assert idem
    records = read_journal_records(journal_dir, "op-001")
    assert len(records) == 1
    assert records[0]["record_type"] == "completed"
    assert records[0]["idempotency_hash"] == idem


def test_journal_read_returns_empty_for_missing_operation(tmp_path: Path) -> None:
    assert read_journal_records(tmp_path / "nonexistent", "op-xyz") == []


def test_journal_multiple_records_appended_in_order(tmp_path: Path) -> None:
    journal_dir = tmp_path / "journal"
    write_started_record(
        journal_dir, "op-001", "step-a", {"n": 1}, ("a",)
    )
    write_completed_record(
        journal_dir, "op-001", "step-a", {"n": 1}, ("a",)
    )
    write_started_record(
        journal_dir, "op-001", "step-b", {"n": 2}, ("b",)
    )
    write_completed_record(
        journal_dir, "op-001", "step-b", {"n": 2}, ("b",)
    )
    records = read_journal_records(journal_dir, "op-001")
    assert len(records) == 4
    assert [r["record_type"] for r in records] == [
        "started", "completed", "started", "completed"
    ]
    assert [r["step_id"] for r in records] == [
        "step-a", "step-a", "step-b", "step-b"
    ]


def test_journal_completed_hashes_by_step_groups_correctly(tmp_path: Path) -> None:
    journal_dir = tmp_path / "journal"
    h1 = write_completed_record(
        journal_dir, "op-001", "step-a", {"n": 1}, ("a",)
    )
    h2 = write_completed_record(
        journal_dir, "op-001", "step-b", {"n": 2}, ("b",)
    )
    by_step = completed_hashes_by_step(journal_dir, "op-001")
    assert by_step == {"step-a": {h1}, "step-b": {h2}}


def test_journal_has_started_record_detects_interrupted_step(tmp_path: Path) -> None:
    journal_dir = tmp_path / "journal"
    write_started_record(journal_dir, "op-001", "step-a", {}, ())
    # started but not completed -> True
    assert has_started_record(journal_dir, "op-001", "step-a") is True


def test_journal_has_started_record_returns_false_after_completion(tmp_path: Path) -> None:
    journal_dir = tmp_path / "journal"
    write_started_record(journal_dir, "op-001", "step-a", {}, ())
    write_completed_record(journal_dir, "op-001", "step-a", {}, ())
    # started then completed -> False
    assert has_started_record(journal_dir, "op-001", "step-a") is False


def test_journal_has_started_record_unknown_step(tmp_path: Path) -> None:
    journal_dir = tmp_path / "journal"
    assert has_started_record(journal_dir, "op-001", "step-missing") is False


# ---------------------------------------------------------------------------
# Idempotency hash unit tests
# ---------------------------------------------------------------------------


def test_idempotency_hash_stable_for_same_inputs() -> None:
    h1 = compute_idempotency_hash(
        "op-001", "step-a", {"store_id": "s1"}, ("a-1", "a-2")
    )
    h2 = compute_idempotency_hash(
        "op-001", "step-a", {"store_id": "s1"}, ("a-1", "a-2")
    )
    assert h1 == h2


def test_idempotency_hash_differs_for_different_operation_id() -> None:
    h1 = compute_idempotency_hash(
        "op-001", "step-a", {}, ()
    )
    h2 = compute_idempotency_hash(
        "op-002", "step-a", {}, ()
    )
    assert h1 != h2


def test_idempotency_hash_differs_for_different_step_id() -> None:
    h1 = compute_idempotency_hash(
        "op-001", "step-a", {}, ()
    )
    h2 = compute_idempotency_hash(
        "op-001", "step-b", {}, ()
    )
    assert h1 != h2


def test_idempotency_hash_differs_for_different_params() -> None:
    h1 = compute_idempotency_hash(
        "op-001", "step-a", {"key": "v1"}, ()
    )
    h2 = compute_idempotency_hash(
        "op-001", "step-a", {"key": "v2"}, ()
    )
    assert h1 != h2


def test_idempotency_hash_differs_for_different_artifact_ids() -> None:
    h1 = compute_idempotency_hash(
        "op-001", "step-a", {}, ("a-1",)
    )
    h2 = compute_idempotency_hash(
        "op-001", "step-a", {}, ("a-2",)
    )
    assert h1 != h2


def test_idempotency_hash_ordering_stable_for_artifact_ids() -> None:
    h1 = compute_idempotency_hash(
        "op-001", "step-a", {}, ("a-2", "a-1")
    )
    h2 = compute_idempotency_hash(
        "op-001", "step-a", {}, ("a-1", "a-2")
    )
    assert h1 == h2


def test_idempotency_hash_ordering_stable_for_params() -> None:
    h1 = compute_idempotency_hash(
        "op-001", "step-a", {"z": 1, "a": 2}, ()
    )
    h2 = compute_idempotency_hash(
        "op-001", "step-a", {"a": 2, "z": 1}, ()
    )
    assert h1 == h2


# ---------------------------------------------------------------------------
# Interpreter journal integration tests
# ---------------------------------------------------------------------------


def _stage_result_for_interpreter_test(
    run_id: str = "run-journal-test",
) -> StageResultEnvelope:
    return StageResultEnvelope(
        run_id=run_id,
        plane="execution",
        stage="builder",
        node_id="builder",
        stage_kind_id="builder",
        work_item_kind="task",
        work_item_id="task-journal-test",
        terminal_result="BUILDER_COMPLETE",
        result_class=ResultClass.SUCCESS,
        summary_status_marker="### BUILDER_COMPLETE",
        success=True,
        started_at=NOW,
        completed_at=NOW,
    )


def _make_mutating_step(
    step_id: str,
    primitive_id: str,
    store_id: str | None = None,
    writes_store: bool = True,
    reads_artifact_ids: tuple[str, ...] = (),
    params: dict | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        step_id=step_id,
        primitive_id=primitive_id,
        mutation_phase="partial_mutation",
        reads_artifact_ids=reads_artifact_ids,
        store_id=store_id,
        writes_store=writes_store,
        input_bindings={},
        params=params or {},
    )


def _make_non_mutating_step(
    step_id: str,
    primitive_id: str,
    reads_artifact_ids: tuple[str, ...] = (),
) -> SimpleNamespace:
    return SimpleNamespace(
        step_id=step_id,
        primitive_id=primitive_id,
        mutation_phase="pre_mutation",
        reads_artifact_ids=reads_artifact_ids,
        store_id=None,
        writes_store=False,
        input_bindings={},
        params={},
    )


def _interpreter_journal_workspace(tmp_path: Path):
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    # Create a dummy artifact so artifact_presence passes
    (run_dir / "input.json").write_text('{"msg": "hello"}\n', encoding="utf-8")
    return paths, run_dir


def test_interpreter_writes_journal_records_for_mutating_steps(
    tmp_path: Path,
) -> None:
    """Interpreter writes started + completed journal records for
    persist_record and enqueue_work_items primitives."""
    paths, run_dir = _interpreter_journal_workspace(tmp_path)
    stage_result = _stage_result_for_interpreter_test()

    steps = (
        _make_non_mutating_step("presence", "artifact_presence", ("input",)),
        _make_mutating_step(
            "persist", "persist_record",
            store_id="test_store",
            reads_artifact_ids=("input",),
        ),
        _make_mutating_step(
            "enqueue", "enqueue_work_items",
            reads_artifact_ids=("input",),
            params={"family_id": "task", "work_item_prefix": "child"},
        ),
    )

    operation_def = SimpleNamespace(
        operation_id="op-journal-001",
        steps=steps,
        failure_mappings=(),
        mutation_journal=None,
    )
    compiled_plan = SimpleNamespace(
        runtime_effect_operations_by_id={"op-journal-001": operation_def},
        artifact_contracts_by_id={
            "input": SimpleNamespace(
                canonical_filename="input.json", accepted_filenames=()
            )
        },
        effect_stores_by_id={
            "test_store": SimpleNamespace(
                runtime_relative_root="test-records"
            )
        },
        work_item_families_by_id={
            "task": SimpleNamespace(
                queue_dirs=SimpleNamespace(
                    queue=str(paths.tasks_queue_dir.relative_to(paths.root))
                )
            )
        },
        runtime_effect_runners_by_id={},
        runtime_effect_rules=(),
        runtime_failure_policies_by_id={},
    )

    result = interpret_operation(
        paths,
        stage_result,
        run_dir,
        compiled_plan,
        operation_id="op-journal-001",
        runner_id=INTERPRETED_RUNNER_ID,
    )

    assert result.decision is RuntimeEffectDecision.CONTINUE_ROUTE

    # Journal records should exist
    journal_records = read_journal_records(
        paths.runtime_effect_journal_dir, "op-journal-001"
    )
    assert len(journal_records) == 4  # started+completed for each of 2 mutating steps
    types = [r["record_type"] for r in journal_records]
    assert types == ["started", "completed", "started", "completed"]
    assert journal_records[0]["step_id"] == "persist"
    assert journal_records[1]["step_id"] == "persist"
    assert "idempotency_hash" in journal_records[1]
    assert journal_records[2]["step_id"] == "enqueue"
    assert journal_records[3]["step_id"] == "enqueue"
    assert "idempotency_hash" in journal_records[3]


def test_interpreter_journal_records_are_jsonl_format(
    tmp_path: Path,
) -> None:
    """Journal file is valid JSONL with one JSON object per line."""
    paths, run_dir = _interpreter_journal_workspace(tmp_path)
    stage_result = _stage_result_for_interpreter_test()

    steps = (
        _make_mutating_step(
            "persist", "persist_record",
            store_id="test_store",
            reads_artifact_ids=("input",),
        ),
    )
    operation_def = SimpleNamespace(
        operation_id="op-jsonl-001",
        steps=steps,
        failure_mappings=(),
        mutation_journal=None,
    )
    compiled_plan = SimpleNamespace(
        runtime_effect_operations_by_id={"op-jsonl-001": operation_def},
        artifact_contracts_by_id={
            "input": SimpleNamespace(
                canonical_filename="input.json", accepted_filenames=()
            )
        },
        effect_stores_by_id={
            "test_store": SimpleNamespace(
                runtime_relative_root="test-records"
            )
        },
        work_item_families_by_id={},
        runtime_effect_runners_by_id={},
        runtime_effect_rules=(),
        runtime_failure_policies_by_id={},
    )

    interpret_operation(
        paths, stage_result, run_dir, compiled_plan,
        operation_id="op-jsonl-001",
        runner_id=INTERPRETED_RUNNER_ID,
    )

    journal_path = paths.runtime_effect_journal_dir / "op-jsonl-001.jsonl"
    assert journal_path.exists()
    lines = journal_path.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 2
    for line in lines:
        obj = json.loads(line)
        assert "record_type" in obj


# ---------------------------------------------------------------------------
# Idempotent resume tests
# ---------------------------------------------------------------------------


def _build_idempotent_resume_context(
    tmp_path: Path,
    operation_id: str = "op-resume-001",
):
    """Build a workspace, run_dir, stage_result, compiled_plan, and paths
    for idempotent resume tests with one persist and one enqueue step."""
    paths, run_dir = _interpreter_journal_workspace(tmp_path)
    stage_result = _stage_result_for_interpreter_test()

    steps = (
        _make_non_mutating_step("presence", "artifact_presence", ("input",)),
        _make_mutating_step(
            "persist", "persist_record",
            store_id="test_store",
            reads_artifact_ids=("input",),
        ),
        _make_mutating_step(
            "enqueue", "enqueue_work_items",
            reads_artifact_ids=("input",),
            params={"family_id": "task", "work_item_prefix": "child"},
        ),
    )
    operation_def = SimpleNamespace(
        operation_id=operation_id,
        steps=steps,
        failure_mappings=(),
        mutation_journal=None,
    )
    compiled_plan = SimpleNamespace(
        runtime_effect_operations_by_id={operation_id: operation_def},
        artifact_contracts_by_id={
            "input": SimpleNamespace(
                canonical_filename="input.json", accepted_filenames=()
            )
        },
        effect_stores_by_id={
            "test_store": SimpleNamespace(
                runtime_relative_root="test-records"
            )
        },
        work_item_families_by_id={
            "task": SimpleNamespace(
                queue_dirs=SimpleNamespace(
                    queue=str(paths.tasks_queue_dir.relative_to(paths.root))
                )
            )
        },
        runtime_effect_runners_by_id={},
        runtime_effect_rules=(),
        runtime_failure_policies_by_id={},
    )
    return paths, run_dir, stage_result, compiled_plan


def test_interpreter_idempotent_resume_skips_completed_steps(
    tmp_path: Path,
) -> None:
    """Second execution with same params should skip already-completed
    mutating steps."""
    paths, run_dir, stage_result, compiled_plan = (
        _build_idempotent_resume_context(tmp_path)
    )

    # First run: executes everything
    result1 = interpret_operation(
        paths, stage_result, run_dir, compiled_plan,
        operation_id="op-resume-001",
        runner_id=INTERPRETED_RUNNER_ID,
    )
    assert result1.decision is RuntimeEffectDecision.CONTINUE_ROUTE
    assert len(result1.created_paths) == 2  # one persist + one enqueue

    # Second run: should skip both mutating steps
    # Remove artifacts created by first run so we can detect if
    # the steps re-ran.
    for p in result1.created_paths:
        Path(p).unlink(missing_ok=True)

    result2 = interpret_operation(
        paths, stage_result, run_dir, compiled_plan,
        operation_id="op-resume-001",
        runner_id=INTERPRETED_RUNNER_ID,
    )
    assert result2.decision is RuntimeEffectDecision.CONTINUE_ROUTE
    # Since both mutating steps were skipped, no new paths should be created.
    assert len(result2.created_paths) == 0


def test_interpreter_idempotent_resume_still_runs_non_mutating_steps(
    tmp_path: Path,
) -> None:
    """Non-mutating steps (artifact_presence, artifact_model_parse) always
    re-run even when journal has completed records."""
    paths, run_dir, stage_result, compiled_plan = (
        _build_idempotent_resume_context(tmp_path, operation_id="op-resume-002")
    )

    # First run
    result1 = interpret_operation(
        paths, stage_result, run_dir, compiled_plan,
        operation_id="op-resume-002",
        runner_id=INTERPRETED_RUNNER_ID,
    )
    assert result1.decision is RuntimeEffectDecision.CONTINUE_ROUTE

    # Remove the input artifact so artifact_presence fails
    (run_dir / "input.json").unlink()

    # Second run: artifact_presence should fail even though journal exists
    result2 = interpret_operation(
        paths, stage_result, run_dir, compiled_plan,
        operation_id="op-resume-002",
        runner_id=INTERPRETED_RUNNER_ID,
    )
    assert result2.decision is RuntimeEffectDecision.REQUEST_BLOCK_SOURCE
    assert result2.failure_class == "required_artifact_missing"


def test_interpreter_idempotency_conflict_on_different_params(
    tmp_path: Path,
) -> None:
    """When a completed journal record exists for a step with a different
    idempotency hash, the interpreter must fail with
    interpreted_idempotency_conflict."""
    paths, run_dir, stage_result, compiled_plan = (
        _build_idempotent_resume_context(tmp_path, operation_id="op-conflict-001")
    )

    # First run with original params
    result1 = interpret_operation(
        paths, stage_result, run_dir, compiled_plan,
        operation_id="op-conflict-001",
        runner_id=INTERPRETED_RUNNER_ID,
    )
    assert result1.decision is RuntimeEffectDecision.CONTINUE_ROUTE

    # Build a new compiled plan with different params for the persist step
    steps2 = (
        _make_non_mutating_step("presence", "artifact_presence", ("input",)),
        _make_mutating_step(
            "persist", "persist_record",
            store_id="test_store",  # same store, same step_id
            reads_artifact_ids=("input",),
            params={"extra_key": "different"},  # different params!
        ),
        _make_mutating_step(
            "enqueue", "enqueue_work_items",
            reads_artifact_ids=("input",),
            params={"family_id": "task", "work_item_prefix": "child"},
        ),
    )
    operation_def2 = SimpleNamespace(
        operation_id="op-conflict-001",
        steps=steps2,
        failure_mappings=(),
        mutation_journal=None,
    )
    compiled_plan2 = SimpleNamespace(
        runtime_effect_operations_by_id={"op-conflict-001": operation_def2},
        artifact_contracts_by_id={
            "input": SimpleNamespace(
                canonical_filename="input.json", accepted_filenames=()
            )
        },
        effect_stores_by_id={
            "test_store": SimpleNamespace(
                runtime_relative_root="test-records"
            )
        },
        work_item_families_by_id={
            "task": SimpleNamespace(
                queue_dirs=SimpleNamespace(
                    queue=str(paths.tasks_queue_dir.relative_to(paths.root))
                )
            )
        },
        runtime_effect_runners_by_id={},
        runtime_effect_rules=(),
        runtime_failure_policies_by_id={},
    )

    # Restore input artifact
    (run_dir / "input.json").write_text('{"msg": "hello"}\n', encoding="utf-8")

    result2 = interpret_operation(
        paths, stage_result, run_dir, compiled_plan2,
        operation_id="op-conflict-001",
        runner_id=INTERPRETED_RUNNER_ID,
    )
    assert result2.decision is RuntimeEffectDecision.REQUEST_BLOCK_SOURCE
    assert result2.failure_class == "interpreted_idempotency_conflict"


def test_interpreter_idempotency_conflict_routed_through_failure_mappings(
    tmp_path: Path,
) -> None:
    """When failure_mappings remap interpreted_idempotency_conflict, the
    mapped class flows through."""
    paths, run_dir, stage_result, compiled_plan = (
        _build_idempotent_resume_context(tmp_path)
    )

    # First run
    result1 = interpret_operation(
        paths, stage_result, run_dir, compiled_plan,
        operation_id="op-resume-001",
        runner_id=INTERPRETED_RUNNER_ID,
    )
    assert result1.decision is RuntimeEffectDecision.CONTINUE_ROUTE

    # Build new plan with different params AND failure_mappings
    steps2 = (
        _make_mutating_step(
            "persist", "persist_record",
            store_id="test_store",
            reads_artifact_ids=("input",),
            params={"extra": "changed"},
        ),
    )
    operation_def2 = SimpleNamespace(
        operation_id="op-resume-001",
        steps=steps2,
        failure_mappings=(
            SimpleNamespace(
                failure_class="interpreted_idempotency_conflict",
                mutation_phase="partial_mutation",
            ),
        ),
        mutation_journal=None,
    )
    compiled_plan2 = SimpleNamespace(
        runtime_effect_operations_by_id={"op-resume-001": operation_def2},
        artifact_contracts_by_id={
            "input": SimpleNamespace(
                canonical_filename="input.json", accepted_filenames=()
            )
        },
        effect_stores_by_id={
            "test_store": SimpleNamespace(
                runtime_relative_root="test-records"
            )
        },
        work_item_families_by_id={},
        runtime_effect_runners_by_id={},
        runtime_effect_rules=(),
        runtime_failure_policies_by_id={},
    )

    (run_dir / "input.json").write_text('{"msg": "hello"}\n', encoding="utf-8")
    result2 = interpret_operation(
        paths, stage_result, run_dir, compiled_plan2,
        operation_id="op-resume-001",
        runner_id=INTERPRETED_RUNNER_ID,
    )
    assert result2.decision is RuntimeEffectDecision.REQUEST_BLOCK_SOURCE
    assert result2.failure_class == "interpreted_idempotency_conflict"


def test_interpreter_resume_with_matching_hash_skips_only_that_step(
    tmp_path: Path,
) -> None:
    """When only one of two mutating steps has a completed journal record,
    the completed one is skipped and the other runs."""
    paths, run_dir, stage_result, compiled_plan = (
        _build_idempotent_resume_context(tmp_path)
    )

    # Pre-populate a completed record for only the persist step.
    # The params must match what _resolve_step_params produces (only
    # store_id, no record_key — the executor defaults record_key to
    # step_id at runtime).
    journal_dir = paths.runtime_effect_journal_dir
    write_completed_record(
        journal_dir, "op-partial-001", "persist",
        params={"store_id": "test_store"},
        reads_artifact_ids=("input",),
    )

    # Custom plan with only one op
    steps = (
        _make_non_mutating_step("presence", "artifact_presence", ("input",)),
        _make_mutating_step(
            "persist", "persist_record",
            store_id="test_store",
            reads_artifact_ids=("input",),
        ),
        _make_mutating_step(
            "enqueue", "enqueue_work_items",
            reads_artifact_ids=("input",),
            params={"family_id": "task", "work_item_prefix": "child"},
        ),
    )
    operation_def = SimpleNamespace(
        operation_id="op-partial-001",
        steps=steps,
        failure_mappings=(),
        mutation_journal=None,
    )
    compiled_plan_partial = SimpleNamespace(
        runtime_effect_operations_by_id={"op-partial-001": operation_def},
        artifact_contracts_by_id={
            "input": SimpleNamespace(
                canonical_filename="input.json", accepted_filenames=()
            )
        },
        effect_stores_by_id={
            "test_store": SimpleNamespace(
                runtime_relative_root="test-records"
            )
        },
        work_item_families_by_id={
            "task": SimpleNamespace(
                queue_dirs=SimpleNamespace(
                    queue=str(paths.tasks_queue_dir.relative_to(paths.root))
                )
            )
        },
        runtime_effect_runners_by_id={},
        runtime_effect_rules=(),
        runtime_failure_policies_by_id={},
    )

    result = interpret_operation(
        paths, stage_result, run_dir, compiled_plan_partial,
        operation_id="op-partial-001",
        runner_id=INTERPRETED_RUNNER_ID,
    )

    assert result.decision is RuntimeEffectDecision.CONTINUE_ROUTE
    # persist was skipped; only enqueue created a path
    assert len(result.created_paths) == 1

    # Journal now has 3 entries: the pre-populated completed, plus
    # started+completed for enqueue
    records = read_journal_records(journal_dir, "op-partial-001")
    assert len(records) == 3


def test_interpreter_non_mutating_primitives_do_not_create_journal_entries(
    tmp_path: Path,
) -> None:
    """artifact_presence and artifact_model_parse should not produce
    journal entries."""
    paths, run_dir = _interpreter_journal_workspace(tmp_path)
    stage_result = _stage_result_for_interpreter_test()

    steps = (
        _make_non_mutating_step("presence", "artifact_presence", ("input",)),
        _make_non_mutating_step("parse", "artifact_model_parse", ("input",)),
    )
    operation_def = SimpleNamespace(
        operation_id="op-nonmut-001",
        steps=steps,
        failure_mappings=(),
        mutation_journal=None,
    )
    compiled_plan = SimpleNamespace(
        runtime_effect_operations_by_id={"op-nonmut-001": operation_def},
        artifact_contracts_by_id={
            "input": SimpleNamespace(
                canonical_filename="input.json", accepted_filenames=()
            )
        },
        effect_stores_by_id={},
        work_item_families_by_id={},
        runtime_effect_runners_by_id={},
        runtime_effect_rules=(),
        runtime_failure_policies_by_id={},
    )

    result = interpret_operation(
        paths, stage_result, run_dir, compiled_plan,
        operation_id="op-nonmut-001",
        runner_id=INTERPRETED_RUNNER_ID,
    )
    assert result.decision is RuntimeEffectDecision.CONTINUE_ROUTE

    records = read_journal_records(
        paths.runtime_effect_journal_dir, "op-nonmut-001"
    )
    assert records == []


# ---------------------------------------------------------------------------
# End-to-end: test_interpreted_artifact_to_child fixture operation
# ---------------------------------------------------------------------------


def _fixture_interpreted_artifact_to_child_operation():
    """Return the compiled operation definition matching
    test_interpreted_artifact_to_child."""
    return SimpleNamespace(
        operation_id="test_interpreted_artifact_to_child",
        steps=(
            SimpleNamespace(
                step_id="validate_required_artifacts",
                primitive_id="artifact_presence",
                mutation_phase="pre_mutation",
                reads_artifact_ids=("test_interpreted_input",),
                validator_ids=(
                    "test_interpreted_artifact_to_child.required_artifacts",
                ),
                store_id=None,
                writes_store=False,
                input_bindings={},
                params={},
                output_context_key=None,
                context_read_key=None,
            ),
            SimpleNamespace(
                step_id="parse_input_artifact",
                primitive_id="artifact_model_parse",
                mutation_phase="pre_mutation",
                reads_artifact_ids=("test_interpreted_input",),
                validator_ids=(
                    "test_interpreted_artifact_to_child.input_schema",
                ),
                store_id=None,
                writes_store=False,
                input_bindings={},
                params={"contract_id": "test_interpreted_input"},
                output_context_key=None,
                context_read_key=None,
            ),
            SimpleNamespace(
                step_id="persist_interpreted_record",
                primitive_id="persist_record",
                mutation_phase="partial_mutation",
                reads_artifact_ids=("test_interpreted_input",),
                validator_ids=(),
                store_id="test_interpreted_records",
                writes_store=True,
                input_bindings={},
                params={
                    "store_id": "test_interpreted_records",
                    "record_key": "persist_interpreted_record",
                },
                output_context_key=None,
                context_read_key=None,
            ),
            SimpleNamespace(
                step_id="enqueue_child_work_item",
                primitive_id="enqueue_work_items",
                mutation_phase="partial_mutation",
                reads_artifact_ids=("test_interpreted_input",),
                validator_ids=(),
                store_id="task_queue",
                writes_store=True,
                input_bindings={},
                params={
                    "family_id": "task",
                    "item_count": 1,
                    "work_item_prefix": "interpreted_child",
                },
                output_context_key=None,
                context_read_key=None,
            ),
        ),
        failure_mappings=(
            SimpleNamespace(
                failure_class="required_artifact_missing",
                mutation_phase="pre_mutation",
                validator_id=(
                    "test_interpreted_artifact_to_child.required_artifacts"
                ),
            ),
            SimpleNamespace(
                failure_class="artifact_parse_artifact_missing",
                mutation_phase="pre_mutation",
                validator_id=None,
            ),
            SimpleNamespace(
                failure_class="interpreted_step_failure",
                mutation_phase="pre_mutation",
                validator_id=None,
            ),
            SimpleNamespace(
                failure_class="interpreted_primitive_unknown",
                mutation_phase="pre_mutation",
                validator_id=None,
            ),
            SimpleNamespace(
                failure_class="interpreted_primitive_error",
                mutation_phase="partial_mutation",
                validator_id=None,
            ),
        ),
        mutation_journal=SimpleNamespace(
            record_step_ids=(
                "persist_interpreted_record",
                "enqueue_child_work_item",
            ),
        ),
    )


def _fixture_interpreted_compiled_plan(paths):
    """Return a compiled_plan SimpleNamespace wired for
    test_interpreted_artifact_to_child."""
    return SimpleNamespace(
        runtime_effect_operations_by_id={
            "test_interpreted_artifact_to_child": (
                _fixture_interpreted_artifact_to_child_operation()
            ),
        },
        runtime_effect_runners_by_id={
            "interpreted_runtime_effect": RuntimeEffectOperationRunnerDefinition(
                runner_id=INTERPRETED_RUNNER_ID,
                operation_ids=("test_interpreted_artifact_to_child",),
                legacy_handler_ids=(),
                legacy_handler_operation_ids={},
                result_display_aliases={},
            ),
        },
        runtime_effect_rules=(
            SimpleNamespace(
                rule_id="test_interpreted_artifact_to_child_on_builder_complete",
                effect_operation_id=(
                    "test_interpreted_artifact_to_child"
                ),
                source_node_id="builder",
                on_outcomes=("BUILDER_COMPLETE",),
                handler_id=None,
                destination_family_id=None,
                required_run_artifacts=("test_interpreted_input",),
                duplicate_policy="fail",
                replay_policy="resume_idempotently",
                partial_commit_policy="block_source",
                lifecycle_mutation_plan_id=None,
                source_completion_lifecycle_mutation_plan_id=None,
                source_blocking_lifecycle_mutation_plan_id=None,
                required_handler_capabilities=(),
            ),
        ),
        artifact_contracts_by_id={
            "test_interpreted_input": SimpleNamespace(
                canonical_filename="test_interpreted_input.json",
                accepted_filenames=(),
            ),
        },
        effect_stores_by_id={
            "test_interpreted_records": SimpleNamespace(
                runtime_relative_root="test-interpreted-records",
            ),
        },
        work_item_families_by_id={
            "task": SimpleNamespace(
                queue_dirs=SimpleNamespace(
                    queue=str(paths.tasks_queue_dir.relative_to(paths.root))
                ),
            ),
        },
        runtime_failure_policies_by_id={},
    )


def _fixture_stage_result() -> StageResultEnvelope:
    return StageResultEnvelope(
        run_id="run-interpreted-e2e",
        plane="execution",
        stage="builder",
        node_id="builder",
        stage_kind_id="builder",
        work_item_kind="task",
        work_item_id="task-interpreted-e2e",
        terminal_result="BUILDER_COMPLETE",
        result_class=ResultClass.SUCCESS,
        summary_status_marker="### BUILDER_COMPLETE",
        success=True,
        started_at=NOW,
        completed_at=NOW,
    )


def test_interpreted_artifact_to_child_end_to_end(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: execute the test_interpreted_artifact_to_child operation
    through the full dispatch path, verify journal records, idempotent
    resume, and handler bypass."""
    paths = _workspace(tmp_path)
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True, exist_ok=True)

    # Create the input artifact.
    input_path = run_dir / "test_interpreted_input.json"
    input_path.write_text(
        json.dumps({"msg": "hello-interpreted", "n": 42}),
        encoding="utf-8",
    )

    stage_result = _fixture_stage_result()
    compiled_plan = _fixture_interpreted_compiled_plan(paths)

    # --- Run 1: full execution ---
    application = apply_runtime_effect_for_stage_result(
        SimpleNamespace(paths=paths, compiled_plan=compiled_plan),
        request=SimpleNamespace(run_dir=str(run_dir)),
        stage_result=stage_result,
        router_decision=RouterDecision(
            action=RouterAction.IDLE,
            next_plane=None,
            next_stage=None,
            reason="builder_complete",
        ),
        compiled_plan=compiled_plan,
    )

    assert application.router_decision.action is RouterAction.IDLE
    assert stage_result.metadata["runtime_effect_operation_id"] == (
        "test_interpreted_artifact_to_child"
    )
    assert stage_result.metadata["runtime_effect_runner_id"] == INTERPRETED_RUNNER_ID
    assert stage_result.metadata["runtime_effect_legacy_handler_id"] is None
    assert stage_result.metadata["runtime_effect_decision"] == "continue_route"

    # Verify child work item was enqueued.
    child_files = sorted(paths.tasks_queue_dir.glob("interpreted_child_*.md"))
    assert len(child_files) >= 1

    # Verify journal records exist.
    journal_records = read_journal_records(
        paths.runtime_effect_journal_dir,
        "test_interpreted_artifact_to_child",
    )
    assert len(journal_records) == 4  # started+completed × 2 mutating steps
    record_types = [r["record_type"] for r in journal_records]
    assert record_types == ["started", "completed", "started", "completed"]

    # --- Run 2: idempotent resume (same params, should skip mutating steps) ---
    # Remove child artifacts created in run 1 so we can detect re-creation.
    for child in child_files:
        child.unlink()

    stage_result2 = _fixture_stage_result().model_copy(
        update={"run_id": "run-interpreted-e2e-resume"}
    )

    application2 = apply_runtime_effect_for_stage_result(
        SimpleNamespace(paths=paths, compiled_plan=compiled_plan),
        request=SimpleNamespace(run_dir=str(run_dir)),
        stage_result=stage_result2,
        router_decision=RouterDecision(
            action=RouterAction.IDLE,
            next_plane=None,
            next_stage=None,
            reason="builder_complete",
        ),
        compiled_plan=compiled_plan,
    )

    assert application2.router_decision.action is RouterAction.IDLE
    # Mutating steps were already completed; should not create new children.
    child_files_after_resume = sorted(
        paths.tasks_queue_dir.glob("interpreted_child_*.md")
    )
    assert len(child_files_after_resume) == 0

    # --- Run 3: handler bypass proof ---
    # Monkeypatch _handler_for_operation to raise; interpreted dispatch
    # must bypass it entirely.
    def _handler_guard(*args, **kwargs):
        raise AssertionError(
            "_handler_for_operation was called but interpreted dispatch "
            "must bypass it"
        )

    monkeypatch.setattr(
        effect_execution,
        "_handler_for_operation",
        _handler_guard,
    )

    stage_result3 = _fixture_stage_result().model_copy(
        update={"run_id": "run-interpreted-e2e-bypass"}
    )

    application3 = apply_runtime_effect_for_stage_result(
        SimpleNamespace(paths=paths, compiled_plan=compiled_plan),
        request=SimpleNamespace(run_dir=str(run_dir)),
        stage_result=stage_result3,
        router_decision=RouterDecision(
            action=RouterAction.IDLE,
            next_plane=None,
            next_stage=None,
            reason="builder_complete",
        ),
        compiled_plan=compiled_plan,
    )

    assert application3.router_decision.action is RouterAction.IDLE
    assert stage_result3.metadata["runtime_effect_operation_id"] == (
        "test_interpreted_artifact_to_child"
    )
    assert stage_result3.metadata["runtime_effect_runner_id"] == INTERPRETED_RUNNER_ID
    # Still no legacy handler calls — bypass intact.
    assert stage_result3.metadata["runtime_effect_legacy_handler_id"] is None
