from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import BaseModel

from millrace_ai.contracts import (
    BlueprintCritiqueDocument,
    BlueprintDraftDocument,
    BlueprintEvaluationDocument,
    BlueprintManifestDocument,
    BlueprintPacketDocument,
    IncidentDecision,
    IncidentDocument,
    Plane,
    PlanningStageName,
    ResultClass,
    SpecDocument,
    StageResultEnvelope,
    TaskDocument,
    WorkItemKind,
)
from millrace_ai.paths import bootstrap_workspace, workspace_paths
from millrace_ai.queue_store import QueueStore
from millrace_ai.runtime import blueprint_effects
from millrace_ai.runtime.effects import (
    RuntimeEffectDecision,
    SourceLifecycleAction,
    apply_runtime_effect_result,
)
from millrace_ai.workspace.blueprint_state import (
    claim_next_blueprint_draft,
    enqueue_blueprint_draft,
    read_blueprint_draft,
    update_active_blueprint_draft,
    write_blueprint_manifest,
)
from millrace_ai.workspace.work_documents import read_work_document_as, render_work_document

NOW = datetime(2026, 5, 19, 12, 0, 0, tzinfo=timezone.utc)


def _workspace(tmp_path: Path):
    return bootstrap_workspace(workspace_paths(tmp_path / "workspace"))


def _run_dir(tmp_path: Path) -> Path:
    path = tmp_path / "run"
    path.mkdir()
    return path


def _write_json(path: Path, payload: object) -> None:
    if isinstance(payload, BaseModel):
        payload = payload.model_dump(mode="json")
    elif isinstance(payload, list):
        payload = [
            item.model_dump(mode="json") if isinstance(item, BaseModel) else item
            for item in payload
        ]
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _spec() -> SpecDocument:
    return SpecDocument(
        spec_id="spec-001",
        title="Configurable workflow loop",
        summary="Build the Blueprint Planning loop.",
        source_type="idea",
        source_id="idea-001",
        root_idea_id="idea-001",
        root_spec_id="spec-001",
        goals=("Blueprint loop is runtime-driven.",),
        constraints=("No direct source mutation from handlers.",),
        target_paths=("src/millrace_ai/runtime/blueprint_effects.py",),
        acceptance=("Blueprint effects preserve lifecycle ordering.",),
        references=("lab/specs/pending/blueprint.md",),
        created_at=NOW,
        created_by="tests",
    )


def _incident() -> IncidentDocument:
    return IncidentDocument(
        incident_id="incident-001",
        title="Blueprint remediation incident",
        summary="Manager Blueprint decomposes an incident.",
        root_idea_id="idea-001",
        root_spec_id="spec-001",
        source_stage=PlanningStageName.AUDITOR,
        source_plane=Plane.PLANNING,
        failure_class="blueprint_gap",
        trigger_reason="Arbiter found a remediation gap.",
        consultant_decision=IncidentDecision.NEEDS_PLANNING,
        opened_at=NOW,
        opened_by="tests",
    )


def _manifest() -> BlueprintManifestDocument:
    return BlueprintManifestDocument(
        manifest_id="manifest-001",
        root_spec_id="spec-001",
        root_idea_id="idea-001",
        source_work_item_kind="spec",
        source_work_item_id="spec-001",
        source_spec_id="spec-001",
        draft_ids=("draft-001", "draft-002"),
        draft_count=2,
        spec_summary="Implement Blueprint runtime effects.",
        decomposition_strategy="Split by runtime mutation boundary.",
        global_acceptance_intent=("Handlers never move source work directly.",),
        integration_boundary_notes=("Use runtime lifecycle intent.",),
        risk_notes=("Partial mutation recovery must be visible.",),
        references=("lab/specs/pending/blueprint.md",),
        created_at=NOW,
    )


def _draft(
    draft_id: str = "draft-001",
    *,
    draft_index: int = 1,
    depends_on_draft_ids: tuple[str, ...] = (),
    current_revision: int = 0,
    latest_blueprint_id: str | None = None,
) -> BlueprintDraftDocument:
    return BlueprintDraftDocument(
        draft_id=draft_id,
        manifest_id="manifest-001",
        root_spec_id="spec-001",
        root_idea_id="idea-001",
        source_spec_id="spec-001",
        draft_index=draft_index,
        depends_on_draft_ids=depends_on_draft_ids,
        title=f"Draft {draft_id}",
        summary="Implement one Blueprint slice.",
        scope=("src/millrace_ai/runtime/blueprint_effects.py",),
        non_goals=("request context generation",),
        target_paths=("src/millrace_ai/runtime/blueprint_effects.py",),
        acceptance_intent=("Runtime effect behavior is deterministic.",),
        verification_intent=("pytest tests/blueprint/test_effects.py -q",),
        dependency_notes=("Previous draft must be approved.",) if depends_on_draft_ids else (),
        integration_boundary_notes=("Queue mutation happens before lifecycle mutation.",),
        context_excerpt="Only runtime effect handlers are in scope.",
        current_revision=current_revision,
        latest_blueprint_id=latest_blueprint_id,
        references=("lab/specs/pending/blueprint.md",),
        created_at=NOW,
    )


def _packet(
    blueprint_id: str = "blueprint-001",
    *,
    revision: int = 1,
) -> BlueprintPacketDocument:
    return BlueprintPacketDocument(
        blueprint_id=blueprint_id,
        draft_id="draft-001",
        manifest_id="manifest-001",
        root_spec_id="spec-001",
        root_idea_id="idea-001",
        revision=revision,
        title="Implement Blueprint runtime effects",
        implementation_scope=("Add runtime effect handlers.",),
        intended_files=("src/millrace_ai/runtime/blueprint_effects.py",),
        design_decisions=("Return lifecycle intent instead of moving source work.",),
        verification_plan=("pytest tests/blueprint/test_effects.py -q",),
        task_acceptance=("Blueprint handlers preserve destination-before-source ordering.",),
        required_checks=("pytest tests/blueprint/test_effects.py -q",),
        risk_notes=("Duplicate promotions must fail.",),
        references=("lab/specs/pending/blueprint.md",),
        created_at=NOW,
    )


def _evaluation(decision: str = "approved") -> BlueprintEvaluationDocument:
    return BlueprintEvaluationDocument(
        evaluation_id="evaluation-001",
        blueprint_id="blueprint-001",
        draft_id="draft-001",
        manifest_id="manifest-001",
        root_spec_id="spec-001",
        root_idea_id="idea-001",
        decision=decision,
        rubric_findings=("Blueprint is coherent.",),
        lineage_consistency_findings=("Lineage matches active draft.",),
        verification_findings=("Checks are concrete.",),
        required_task_fields=("task_id", "target_paths") if decision == "approved" else (),
        critique_id="critique-001" if decision == "rejected" else None,
        references=("lab/specs/pending/blueprint.md",),
        created_at=NOW,
    )


def _critique() -> BlueprintCritiqueDocument:
    return BlueprintCritiqueDocument(
        critique_id="critique-001",
        evaluation_id="evaluation-001",
        blueprint_id="blueprint-001",
        draft_id="draft-001",
        manifest_id="manifest-001",
        root_spec_id="spec-001",
        root_idea_id="idea-001",
        revision=1,
        required_changes=("Narrow the implementation sequence.",),
        verification_issues=("Add a duplicate promotion check.",),
        blocking_reason="Blueprint needs a narrower implementation sequence.",
        references=("lab/specs/pending/blueprint.md",),
        created_at=NOW,
    )


def _task() -> TaskDocument:
    return TaskDocument(
        task_id="task-001",
        title="Implement Blueprint runtime effects",
        summary="Add runtime effect handlers for Blueprint Planning.",
        root_idea_id="idea-001",
        root_spec_id="spec-001",
        spec_id="spec-001",
        target_paths=("src/millrace_ai/runtime/blueprint_effects.py",),
        acceptance=("Blueprint handlers preserve destination-before-source ordering.",),
        required_checks=("pytest tests/blueprint/test_effects.py -q",),
        references=("lab/specs/pending/blueprint.md",),
        risk=("Duplicate promotions must fail.",),
        created_at=NOW,
        created_by="evaluator_blueprint",
    )


def _stage_result(work_item_kind: WorkItemKind, work_item_id: str) -> StageResultEnvelope:
    return StageResultEnvelope(
        run_id="run-001",
        plane="planning",
        stage="manager",
        node_id="manager_blueprint",
        stage_kind_id="manager_blueprint",
        work_item_kind=work_item_kind,
        work_item_id=work_item_id,
        terminal_result="MANAGER_COMPLETE",
        result_class=ResultClass.SUCCESS,
        summary_status_marker="### MANAGER_COMPLETE",
        success=True,
        started_at=NOW,
        completed_at=NOW,
    )


def _activate_source_spec(paths) -> None:
    queue = QueueStore(paths)
    queue.enqueue_spec(_spec())
    assert queue.claim_next_planning_item() is not None


def _activate_source_incident(paths) -> None:
    queue = QueueStore(paths)
    queue.enqueue_incident(_incident())
    assert queue.claim_next_planning_item() is not None


def _activate_blueprint_draft(paths) -> None:
    enqueue_blueprint_draft(paths, _draft())
    assert claim_next_blueprint_draft(paths) is not None


def _persist_candidate(paths: object, run_dir: Path) -> None:
    _write_json(run_dir / "blueprint_packet.json", _packet())
    (run_dir / "blueprint.md").write_text("# Blueprint\n\nImplement runtime effects.\n", encoding="utf-8")
    result = blueprint_effects.contractor_blueprint_candidate_persist(
        paths,
        _stage_result(WorkItemKind.BLUEPRINT_DRAFT, "draft-001"),
        run_dir,
    )
    assert result.decision is RuntimeEffectDecision.CONTINUE_ROUTE


def test_manager_manifest_promotion_enqueues_drafts_and_returns_source_complete_intent(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    _activate_source_spec(paths)
    run_dir = _run_dir(tmp_path)
    _write_json(run_dir / "blueprint_manifest.json", _manifest())
    _write_json(
        run_dir / "blueprint_drafts.json",
        [
            _draft("draft-001", draft_index=1),
            _draft("draft-002", draft_index=2, depends_on_draft_ids=("draft-001",)),
        ],
    )

    result = blueprint_effects.manager_blueprint_manifest_to_blueprint_drafts(
        paths,
        _stage_result(WorkItemKind.SPEC, "spec-001"),
        run_dir,
    )

    assert result.decision is RuntimeEffectDecision.REQUEST_COMPLETE_SOURCE
    assert result.source_lifecycle_intent is not None
    assert result.source_lifecycle_intent.action is SourceLifecycleAction.COMPLETE
    assert result.source_lifecycle_intent.work_item_kind is WorkItemKind.SPEC
    assert (paths.specs_active_dir / "spec-001.md").is_file()
    assert not (paths.specs_done_dir / "spec-001.md").exists()
    assert (paths.runtime_root / "blueprints/manifests/manifest-001.json").is_file()
    assert (paths.runtime_root / "blueprints/drafts/queue/draft-001.json").is_file()
    assert (paths.runtime_root / "blueprints/drafts/queue/draft-002.json").is_file()
    assert all((paths.root / path).exists() for path in result.created_paths)


@pytest.mark.parametrize(
    ("artifact_setup", "expected_failure_class"),
    [
        ("missing_manifest", "blueprint_manifest_missing"),
        ("malformed_manifest", "blueprint_manifest_parse_error"),
        ("invalid_manifest_schema", "blueprint_manifest_schema_invalid"),
        ("missing_drafts", "blueprint_drafts_missing"),
        ("malformed_drafts", "blueprint_drafts_parse_error"),
        ("invalid_drafts_schema", "blueprint_drafts_schema_invalid"),
    ],
)
def test_manager_manifest_promotion_classifies_artifact_failures_precisely(
    tmp_path: Path,
    artifact_setup: str,
    expected_failure_class: str,
) -> None:
    paths = _workspace(tmp_path)
    _activate_source_spec(paths)
    run_dir = _run_dir(tmp_path)
    if artifact_setup != "missing_manifest":
        if artifact_setup == "malformed_manifest":
            (run_dir / "blueprint_manifest.json").write_text("{not json", encoding="utf-8")
        elif artifact_setup == "invalid_manifest_schema":
            _write_json(run_dir / "blueprint_manifest.json", {"manifest_id": "manifest-001"})
        else:
            _write_json(run_dir / "blueprint_manifest.json", _manifest())
    if artifact_setup != "missing_drafts":
        if artifact_setup == "malformed_drafts":
            (run_dir / "blueprint_drafts.json").write_text("[not json", encoding="utf-8")
        elif artifact_setup == "invalid_drafts_schema":
            _write_json(run_dir / "blueprint_drafts.json", [{"draft_id": "draft-001"}])
        else:
            _write_json(
                run_dir / "blueprint_drafts.json",
                [
                    _draft("draft-001", draft_index=1),
                    _draft("draft-002", draft_index=2, depends_on_draft_ids=("draft-001",)),
                ],
            )

    result = blueprint_effects.manager_blueprint_manifest_to_blueprint_drafts(
        paths,
        _stage_result(WorkItemKind.SPEC, "spec-001"),
        run_dir,
    )

    assert result.decision is RuntimeEffectDecision.REQUEST_BLOCK_SOURCE
    assert result.failure_class == expected_failure_class
    assert result.created_paths == ()
    assert not (paths.runtime_root / "blueprints/manifests/manifest-001.json").exists()
    assert not (paths.runtime_root / "blueprints/drafts/queue/draft-001.json").exists()


def test_manager_manifest_promotion_classifies_manifest_draft_mismatch(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    _activate_source_spec(paths)
    run_dir = _run_dir(tmp_path)
    _write_json(run_dir / "blueprint_manifest.json", _manifest())
    _write_json(
        run_dir / "blueprint_drafts.json",
        [
            _draft("draft-001", draft_index=1),
            _draft("draft-002", draft_index=2),
        ],
    )

    result = blueprint_effects.manager_blueprint_manifest_to_blueprint_drafts(
        paths,
        _stage_result(WorkItemKind.SPEC, "spec-001"),
        run_dir,
    )

    assert result.decision is RuntimeEffectDecision.REQUEST_BLOCK_SOURCE
    assert result.failure_class == "blueprint_manifest_draft_mismatch"
    assert result.created_paths == ()


def test_manager_manifest_promotion_classifies_duplicate_manifest_id(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    _activate_source_spec(paths)
    write_blueprint_manifest(
        paths,
        _manifest().model_copy(update={"spec_summary": "Already persisted manifest."}),
    )
    run_dir = _run_dir(tmp_path)
    _write_json(run_dir / "blueprint_manifest.json", _manifest())
    _write_json(
        run_dir / "blueprint_drafts.json",
        [
            _draft("draft-001", draft_index=1),
            _draft("draft-002", draft_index=2, depends_on_draft_ids=("draft-001",)),
        ],
    )

    result = blueprint_effects.manager_blueprint_manifest_to_blueprint_drafts(
        paths,
        _stage_result(WorkItemKind.SPEC, "spec-001"),
        run_dir,
    )

    assert result.decision is RuntimeEffectDecision.REQUEST_BLOCK_SOURCE
    assert result.failure_class == "blueprint_manifest_duplicate"
    assert result.created_paths == ()


def test_manager_manifest_promotion_classifies_duplicate_draft_id(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    _activate_source_spec(paths)
    write_blueprint_manifest(paths, _manifest())
    enqueue_blueprint_draft(
        paths,
        _draft("draft-001", draft_index=1).model_copy(
            update={"summary": "Divergent queued draft."}
        ),
    )
    run_dir = _run_dir(tmp_path)
    _write_json(run_dir / "blueprint_manifest.json", _manifest())
    _write_json(
        run_dir / "blueprint_drafts.json",
        [
            _draft("draft-001", draft_index=1),
            _draft("draft-002", draft_index=2, depends_on_draft_ids=("draft-001",)),
        ],
    )

    result = blueprint_effects.manager_blueprint_manifest_to_blueprint_drafts(
        paths,
        _stage_result(WorkItemKind.SPEC, "spec-001"),
        run_dir,
    )

    assert result.decision is RuntimeEffectDecision.REQUEST_BLOCK_SOURCE
    assert result.failure_class == "blueprint_draft_duplicate"
    assert result.created_paths == ()
    assert not (paths.runtime_root / "blueprints/drafts/queue/draft-002.json").exists()


def test_manager_manifest_promotion_replay_after_source_done_is_noop_success(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    _activate_source_spec(paths)
    run_dir = _run_dir(tmp_path)
    _write_json(run_dir / "blueprint_manifest.json", _manifest())
    _write_json(
        run_dir / "blueprint_drafts.json",
        [
            _draft("draft-001", draft_index=1),
            _draft("draft-002", draft_index=2, depends_on_draft_ids=("draft-001",)),
        ],
    )
    first = blueprint_effects.manager_blueprint_manifest_to_blueprint_drafts(
        paths,
        _stage_result(WorkItemKind.SPEC, "spec-001"),
        run_dir,
    )
    apply_runtime_effect_result(paths, first)

    replay = blueprint_effects.manager_blueprint_manifest_to_blueprint_drafts(
        paths,
        _stage_result(WorkItemKind.SPEC, "spec-001"),
        run_dir,
    )

    assert replay.decision is RuntimeEffectDecision.REQUEST_COMPLETE_SOURCE
    assert replay.failure_class is None
    assert replay.source_lifecycle_intent is None
    assert replay.created_paths == ()
    assert (paths.specs_done_dir / "spec-001.md").is_file()


def test_manager_manifest_promotion_replay_accepts_progressed_draft_state(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    _activate_source_spec(paths)
    run_dir = _run_dir(tmp_path)
    _write_json(run_dir / "blueprint_manifest.json", _manifest())
    _write_json(
        run_dir / "blueprint_drafts.json",
        [
            _draft("draft-001", draft_index=1),
            _draft("draft-002", draft_index=2, depends_on_draft_ids=("draft-001",)),
        ],
    )
    first = blueprint_effects.manager_blueprint_manifest_to_blueprint_drafts(
        paths,
        _stage_result(WorkItemKind.SPEC, "spec-001"),
        run_dir,
    )
    apply_runtime_effect_result(paths, first)
    assert claim_next_blueprint_draft(paths) is not None
    active_draft = read_blueprint_draft(
        paths.runtime_root / "blueprints/drafts/active/draft-001.json"
    )
    update_active_blueprint_draft(
        paths,
        active_draft.model_copy(
            update={
                "current_revision": 1,
                "latest_blueprint_id": "blueprint-001",
                "latest_critique_id": "critique-001",
                "updated_at": NOW,
            }
        ),
    )

    replay = blueprint_effects.manager_blueprint_manifest_to_blueprint_drafts(
        paths,
        _stage_result(WorkItemKind.SPEC, "spec-001"),
        run_dir,
    )

    assert replay.decision is RuntimeEffectDecision.REQUEST_COMPLETE_SOURCE
    assert replay.failure_class is None
    assert replay.source_lifecycle_intent is None
    assert replay.created_paths == ()
    assert (paths.specs_done_dir / "spec-001.md").is_file()


def test_manager_manifest_promotion_replay_after_incident_resolved_is_noop_success(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    _activate_source_incident(paths)
    incident_manifest = _manifest().model_copy(
        update={
            "source_work_item_kind": "incident",
            "source_work_item_id": "incident-001",
        }
    )
    run_dir = _run_dir(tmp_path)
    _write_json(run_dir / "blueprint_manifest.json", incident_manifest)
    _write_json(
        run_dir / "blueprint_drafts.json",
        [
            _draft("draft-001", draft_index=1),
            _draft("draft-002", draft_index=2, depends_on_draft_ids=("draft-001",)),
        ],
    )
    first = blueprint_effects.manager_blueprint_manifest_to_blueprint_drafts(
        paths,
        _stage_result(WorkItemKind.INCIDENT, "incident-001"),
        run_dir,
    )
    apply_runtime_effect_result(paths, first)

    replay = blueprint_effects.manager_blueprint_manifest_to_blueprint_drafts(
        paths,
        _stage_result(WorkItemKind.INCIDENT, "incident-001"),
        run_dir,
    )

    assert replay.decision is RuntimeEffectDecision.REQUEST_COMPLETE_SOURCE
    assert replay.failure_class is None
    assert replay.source_lifecycle_intent is None
    assert replay.created_paths == ()
    assert (paths.incidents_resolved_dir / "incident-001.md").is_file()


@pytest.mark.parametrize(
    ("artifact_name", "expected_failure_class"),
    [
        ("blueprint_manifest.json", "blueprint_manifest_missing"),
        ("blueprint_drafts.json", "blueprint_drafts_missing"),
    ],
)
def test_manager_manifest_promotion_maps_artifact_read_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    artifact_name: str,
    expected_failure_class: str,
) -> None:
    paths = _workspace(tmp_path)
    _activate_source_spec(paths)
    run_dir = _run_dir(tmp_path)
    _write_json(run_dir / "blueprint_manifest.json", _manifest())
    _write_json(
        run_dir / "blueprint_drafts.json",
        [
            _draft("draft-001", draft_index=1),
            _draft("draft-002", draft_index=2, depends_on_draft_ids=("draft-001",)),
        ],
    )
    original_read_text = Path.read_text

    def fail_selected_artifact(path: Path, *args, **kwargs):
        if path.name == artifact_name:
            raise OSError(f"simulated read race for {artifact_name}")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fail_selected_artifact)

    result = blueprint_effects.manager_blueprint_manifest_to_blueprint_drafts(
        paths,
        _stage_result(WorkItemKind.SPEC, "spec-001"),
        run_dir,
    )

    assert result.decision is RuntimeEffectDecision.REQUEST_BLOCK_SOURCE
    assert result.failure_class == expected_failure_class
    assert result.created_paths == ()


def test_manager_manifest_promotion_finishes_partial_replay_when_outputs_match(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    _activate_source_spec(paths)
    write_blueprint_manifest(paths, _manifest())
    enqueue_blueprint_draft(paths, _draft("draft-001", draft_index=1))
    run_dir = _run_dir(tmp_path)
    _write_json(run_dir / "blueprint_manifest.json", _manifest())
    _write_json(
        run_dir / "blueprint_drafts.json",
        [
            _draft("draft-001", draft_index=1),
            _draft("draft-002", draft_index=2, depends_on_draft_ids=("draft-001",)),
        ],
    )

    result = blueprint_effects.manager_blueprint_manifest_to_blueprint_drafts(
        paths,
        _stage_result(WorkItemKind.SPEC, "spec-001"),
        run_dir,
    )

    assert result.decision is RuntimeEffectDecision.REQUEST_COMPLETE_SOURCE
    assert result.failure_class is None
    assert result.source_lifecycle_intent is not None
    assert result.source_lifecycle_intent.action is SourceLifecycleAction.COMPLETE
    assert result.created_paths == ("millrace-agents/blueprints/drafts/queue/draft-002.json",)
    assert (paths.runtime_root / "blueprints/drafts/queue/draft-002.json").is_file()


def test_manager_manifest_promotion_reports_source_lifecycle_invalid_after_validation(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    run_dir = _run_dir(tmp_path)
    _write_json(run_dir / "blueprint_manifest.json", _manifest())
    _write_json(
        run_dir / "blueprint_drafts.json",
        [
            _draft("draft-001", draft_index=1),
            _draft("draft-002", draft_index=2, depends_on_draft_ids=("draft-001",)),
        ],
    )

    result = blueprint_effects.manager_blueprint_manifest_to_blueprint_drafts(
        paths,
        _stage_result(WorkItemKind.SPEC, "spec-001"),
        run_dir,
    )

    assert result.decision is RuntimeEffectDecision.REQUEST_BLOCK_SOURCE
    assert result.failure_class == "blueprint_source_lifecycle_invalid"
    assert result.source_lifecycle_intent is None
    assert result.created_paths == ()
    assert not (paths.runtime_root / "blueprints/manifests/manifest-001.json").exists()


def test_contractor_candidate_persistence_updates_active_draft_after_durable_writes(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    _activate_blueprint_draft(paths)
    run_dir = _run_dir(tmp_path)
    _write_json(run_dir / "blueprint_packet.json", _packet())
    (run_dir / "blueprint.md").write_text("# Blueprint\n\nImplement runtime effects.\n", encoding="utf-8")

    result = blueprint_effects.contractor_blueprint_candidate_persist(
        paths,
        _stage_result(WorkItemKind.BLUEPRINT_DRAFT, "draft-001"),
        run_dir,
    )

    packet_path = paths.runtime_root / "blueprints/packets/candidates/blueprint-001.json"
    markdown_path = paths.runtime_root / "blueprints/packets/candidates/blueprint-001.md"
    active_draft = read_blueprint_draft(paths.runtime_root / "blueprints/drafts/active/draft-001.json")
    assert result.decision is RuntimeEffectDecision.CONTINUE_ROUTE
    assert result.source_lifecycle_intent is None
    assert packet_path.is_file()
    assert markdown_path.is_file()
    assert active_draft.latest_blueprint_id == "blueprint-001"
    assert active_draft.current_revision == 0
    assert all((paths.root / path).exists() for path in result.created_paths)


def test_evaluator_approval_enqueues_one_task_and_returns_draft_approval_intent(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    _activate_blueprint_draft(paths)
    run_dir = _run_dir(tmp_path)
    _persist_candidate(paths, run_dir)
    _write_json(run_dir / "blueprint_evaluation.json", _evaluation("approved"))
    (run_dir / "generated_task.md").write_text(render_work_document(_task()), encoding="utf-8")

    result = blueprint_effects.evaluator_blueprint_approved_to_task(
        paths,
        _stage_result(WorkItemKind.BLUEPRINT_DRAFT, "draft-001"),
        run_dir,
    )

    assert result.decision is RuntimeEffectDecision.REQUEST_COMPLETE_SOURCE
    assert result.source_lifecycle_intent is not None
    assert result.source_lifecycle_intent.work_item_kind is WorkItemKind.BLUEPRINT_DRAFT
    assert result.source_lifecycle_intent.action is SourceLifecycleAction.COMPLETE
    assert len(list(paths.tasks_queue_dir.glob("*.md"))) == 1
    assert (paths.runtime_root / "blueprints/packets/approved/blueprint-001.json").is_file()
    assert not (paths.runtime_root / "blueprints/packets/candidates/blueprint-001.json").exists()
    assert (paths.runtime_root / "blueprints/promotions/promotion-evaluation-001.json").is_file()
    queued_task = read_work_document_as(paths.tasks_queue_dir / "task-001.md", model=TaskDocument)
    assert "millrace-agents/blueprints/packets/approved/blueprint-001.json" in queued_task.references
    assert "millrace-agents/blueprints/evaluations/evaluation-001.json" in queued_task.references

    applied = apply_runtime_effect_result(paths, result)

    assert applied.decision is RuntimeEffectDecision.REQUEST_COMPLETE_SOURCE
    assert (paths.runtime_root / "blueprints/drafts/approved/draft-001.json").is_file()


def test_evaluator_approval_uses_canonical_generated_task_json_when_markdown_is_invalid(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    _activate_blueprint_draft(paths)
    run_dir = _run_dir(tmp_path)
    _persist_candidate(paths, run_dir)
    _write_json(run_dir / "blueprint_evaluation.json", _evaluation("approved"))
    _write_json(run_dir / "generated_task.json", _task())
    (run_dir / "generated_task.md").write_text(
        "# Narrative\n\nThis is not a canonical task document.\n",
        encoding="utf-8",
    )

    result = blueprint_effects.evaluator_blueprint_approved_to_task(
        paths,
        _stage_result(WorkItemKind.BLUEPRINT_DRAFT, "draft-001"),
        run_dir,
    )

    assert result.decision is RuntimeEffectDecision.REQUEST_COMPLETE_SOURCE
    assert (paths.tasks_queue_dir / "task-001.md").is_file()


def test_evaluator_approval_fails_malformed_canonical_generated_task_json_before_fallback(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    _activate_blueprint_draft(paths)
    run_dir = _run_dir(tmp_path)
    _persist_candidate(paths, run_dir)
    _write_json(run_dir / "blueprint_evaluation.json", _evaluation("approved"))
    (run_dir / "generated_task.json").write_text(
        '{"task_id": "task-001", "title": 12}',
        encoding="utf-8",
    )
    (run_dir / "generated_task.md").write_text(render_work_document(_task()), encoding="utf-8")

    result = blueprint_effects.evaluator_blueprint_approved_to_task(
        paths,
        _stage_result(WorkItemKind.BLUEPRINT_DRAFT, "draft-001"),
        run_dir,
    )

    assert result.decision is RuntimeEffectDecision.REQUEST_BLOCK_SOURCE
    assert result.failure_class == "generated_task_invalid"
    assert not (paths.tasks_queue_dir / "task-001.md").exists()


def test_evaluator_rejection_persists_critique_and_keeps_active_draft(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    _activate_blueprint_draft(paths)
    run_dir = _run_dir(tmp_path)
    _persist_candidate(paths, run_dir)
    _write_json(run_dir / "blueprint_evaluation.json", _evaluation("rejected"))
    _write_json(run_dir / "critique_packet.json", _critique())

    result = blueprint_effects.evaluator_blueprint_rejected_to_draft_revision(
        paths,
        _stage_result(WorkItemKind.BLUEPRINT_DRAFT, "draft-001"),
        run_dir,
    )

    active_path = paths.runtime_root / "blueprints/drafts/active/draft-001.json"
    active_draft = read_blueprint_draft(active_path)
    assert result.decision is RuntimeEffectDecision.CONTINUE_ROUTE
    assert result.source_lifecycle_intent is None
    assert (paths.runtime_root / "blueprints/evaluations/evaluation-001.json").is_file()
    assert (paths.runtime_root / "blueprints/critiques/open/critique-001.json").is_file()
    assert (paths.runtime_root / "blueprints/packets/rejected/blueprint-001.json").is_file()
    assert active_draft.status == "active"
    assert active_draft.current_revision == 1
    assert active_draft.latest_critique_id == "critique-001"


def test_evaluator_rejection_uses_canonical_blueprint_critique_json_without_legacy_packet(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    _activate_blueprint_draft(paths)
    run_dir = _run_dir(tmp_path)
    _persist_candidate(paths, run_dir)
    _write_json(run_dir / "blueprint_evaluation.json", _evaluation("rejected"))
    _write_json(run_dir / "blueprint_critique.json", _critique())

    result = blueprint_effects.evaluator_blueprint_rejected_to_draft_revision(
        paths,
        _stage_result(WorkItemKind.BLUEPRINT_DRAFT, "draft-001"),
        run_dir,
    )

    active_path = paths.runtime_root / "blueprints/drafts/active/draft-001.json"
    active_draft = read_blueprint_draft(active_path)
    assert result.decision is RuntimeEffectDecision.CONTINUE_ROUTE
    assert (paths.runtime_root / "blueprints/critiques/open/critique-001.json").is_file()
    assert active_draft.latest_critique_id == "critique-001"


def test_evaluator_rejection_fails_malformed_canonical_critique_before_legacy_fallback(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    _activate_blueprint_draft(paths)
    run_dir = _run_dir(tmp_path)
    _persist_candidate(paths, run_dir)
    _write_json(run_dir / "blueprint_evaluation.json", _evaluation("rejected"))
    (run_dir / "blueprint_critique.json").write_text(
        '{"critique_id": "critique-001", "required_changes": 12}',
        encoding="utf-8",
    )
    _write_json(run_dir / "critique_packet.json", _critique())

    result = blueprint_effects.evaluator_blueprint_rejected_to_draft_revision(
        paths,
        _stage_result(WorkItemKind.BLUEPRINT_DRAFT, "draft-001"),
        run_dir,
    )

    assert result.decision is RuntimeEffectDecision.REQUEST_BLOCK_SOURCE
    assert result.failure_class == "blueprint_critique_invalid"
    assert not (paths.runtime_root / "blueprints/critiques/open/critique-001.json").exists()


def test_manager_partial_write_failure_requests_source_blockage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _workspace(tmp_path)
    _activate_source_spec(paths)
    run_dir = _run_dir(tmp_path)
    _write_json(run_dir / "blueprint_manifest.json", _manifest())
    _write_json(
        run_dir / "blueprint_drafts.json",
        [
            _draft("draft-001", draft_index=1),
            _draft("draft-002", draft_index=2, depends_on_draft_ids=("draft-001",)),
        ],
    )
    original_enqueue = blueprint_effects.enqueue_blueprint_draft

    def fail_second_enqueue(paths_arg, draft):
        if draft.draft_id == "draft-002":
            raise OSError("simulated write failure")
        return original_enqueue(paths_arg, draft)

    monkeypatch.setattr(blueprint_effects, "enqueue_blueprint_draft", fail_second_enqueue)

    result = blueprint_effects.manager_blueprint_manifest_to_blueprint_drafts(
        paths,
        _stage_result(WorkItemKind.SPEC, "spec-001"),
        run_dir,
    )

    assert result.decision is RuntimeEffectDecision.REQUEST_BLOCK_SOURCE
    assert result.failure_class == "blueprint_partial_mutation"
    assert result.source_lifecycle_intent is not None
    assert result.source_lifecycle_intent.action is SourceLifecycleAction.BLOCK
    assert result.source_lifecycle_intent.work_item_kind is WorkItemKind.SPEC
    assert (paths.specs_active_dir / "spec-001.md").is_file()
