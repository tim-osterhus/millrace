from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import BaseModel

from millrace_ai.contracts import (
    BlueprintCritiqueDocument,
    BlueprintDraftDocument,
    BlueprintEvaluationDocument,
    BlueprintManifestDocument,
    BlueprintPacketDocument,
    BlueprintPromotionRecord,
    Plane,
    ResultClass,
    SpecDocument,
    StageResultEnvelope,
    TaskDocument,
    WorkItemKind,
)
from millrace_ai.paths import bootstrap_workspace, workspace_paths
from millrace_ai.runtime import blueprint_effects
from millrace_ai.runtime.effects import RuntimeEffectDecision, RuntimeEffectResult
from millrace_ai.runtime.effects import operations as effect_operations
from millrace_ai.workspace.blueprint_state import (
    enqueue_blueprint_draft,
    persist_blueprint_evaluation,
    persist_blueprint_packet,
    persist_blueprint_promotion,
    read_blueprint_draft,
    write_blueprint_manifest,
)
from millrace_ai.workspace.queue_transitions import enqueue_task
from millrace_ai.workspace.work_documents import render_work_document

NOW = datetime(2026, 5, 26, tzinfo=UTC)
ManagerRunner = Callable[[object, StageResultEnvelope, Path, object | None], RuntimeEffectResult]
ContractorRunner = Callable[[object, StageResultEnvelope, Path, object | None], RuntimeEffectResult]
EvaluatorRunner = Callable[[object, StageResultEnvelope, Path, object | None], RuntimeEffectResult]


def _workspace(tmp_path: Path):
    return bootstrap_workspace(workspace_paths(tmp_path / "workspace"))


def _run_dir(tmp_path: Path) -> Path:
    path = tmp_path / "run"
    path.mkdir(parents=True)
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


def _activate_source_spec(paths) -> None:
    paths.specs_active_dir.mkdir(parents=True, exist_ok=True)
    (paths.specs_active_dir / "spec-001.md").write_text(
        render_work_document(_spec()),
        encoding="utf-8",
    )


def _manifest(**updates: object) -> BlueprintManifestDocument:
    values: dict[str, object] = {
        "manifest_id": "manifest-001",
        "root_spec_id": "spec-001",
        "root_idea_id": "idea-001",
        "source_work_item_kind": "spec",
        "source_work_item_id": "spec-001",
        "source_spec_id": "spec-001",
        "draft_ids": ("draft-001", "draft-002"),
        "draft_count": 2,
        "spec_summary": "Implement Blueprint runtime effects.",
        "decomposition_strategy": "Split by runtime mutation boundary.",
        "global_acceptance_intent": ("Handlers never move source work directly.",),
        "integration_boundary_notes": ("Use runtime lifecycle intent.",),
        "risk_notes": ("Partial mutation recovery must be visible.",),
        "references": ("lab/specs/pending/blueprint.md",),
        "created_at": NOW,
    }
    values.update(updates)
    return BlueprintManifestDocument(**values)


def _draft(
    draft_id: str,
    *,
    draft_index: int,
    depends_on_draft_ids: tuple[str, ...] = (),
    summary: str = "Implement one Blueprint slice.",
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
        summary=summary,
        scope=("src/millrace_ai/runtime/blueprint_effects.py",),
        non_goals=("request context generation",),
        target_paths=("src/millrace_ai/runtime/blueprint_effects.py",),
        acceptance_intent=("Runtime effect behavior is deterministic.",),
        verification_intent=("pytest tests/blueprint/test_effects.py -q",),
        dependency_notes=("Previous draft must be approved.",) if depends_on_draft_ids else (),
        integration_boundary_notes=("Queue mutation happens before lifecycle mutation.",),
        context_excerpt="Only runtime effect handlers are in scope.",
        current_revision=0,
        references=("lab/specs/pending/blueprint.md",),
        created_at=NOW,
    )


def _drafts() -> tuple[BlueprintDraftDocument, BlueprintDraftDocument]:
    return (
        _draft("draft-001", draft_index=1),
        _draft("draft-002", draft_index=2, depends_on_draft_ids=("draft-001",)),
    )


def _stage_result() -> StageResultEnvelope:
    return StageResultEnvelope(
        run_id="run-manager-001",
        plane=Plane.PLANNING,
        stage="manager",
        node_id="manager_blueprint",
        stage_kind_id="manager_blueprint",
        work_item_kind=WorkItemKind.SPEC,
        work_item_id="spec-001",
        terminal_result="MANAGER_BLUEPRINT_COMPLETE",
        result_class=ResultClass.SUCCESS,
        summary_status_marker="### MANAGER_BLUEPRINT_COMPLETE",
        success=True,
        started_at=NOW,
        completed_at=NOW,
    )


def _contractor_stage_result() -> StageResultEnvelope:
    return StageResultEnvelope(
        run_id="run-contractor-001",
        plane=Plane.PLANNING,
        stage="manager",
        node_id="manager_blueprint",
        stage_kind_id="manager_blueprint",
        work_item_kind=WorkItemKind.BLUEPRINT_DRAFT,
        work_item_id="draft-001",
        terminal_result="MANAGER_COMPLETE",
        result_class=ResultClass.SUCCESS,
        summary_status_marker="### MANAGER_COMPLETE",
        success=True,
        started_at=NOW,
        completed_at=NOW,
    )


def _evaluator_stage_result(terminal_result: str = "BLUEPRINT_APPROVED") -> StageResultEnvelope:
    return StageResultEnvelope(
        run_id="run-evaluator-001",
        plane=Plane.PLANNING,
        stage="manager",
        node_id="evaluator_blueprint",
        stage_kind_id="evaluator_blueprint",
        work_item_kind=WorkItemKind.BLUEPRINT_DRAFT,
        work_item_id="draft-001",
        terminal_result=terminal_result,
        result_class=ResultClass.SUCCESS,
        summary_status_marker=f"### {terminal_result}",
        success=True,
        started_at=NOW,
        completed_at=NOW,
    )


def _packet(**updates: object) -> BlueprintPacketDocument:
    values: dict[str, object] = {
        "blueprint_id": "blueprint-001",
        "draft_id": "draft-001",
        "manifest_id": "manifest-001",
        "root_spec_id": "spec-001",
        "root_idea_id": "idea-001",
        "revision": 1,
        "title": "Implement Blueprint runtime effects",
        "implementation_scope": ("Add runtime effect handlers.",),
        "intended_files": ("src/millrace_ai/runtime/blueprint_effects.py",),
        "design_decisions": ("Return lifecycle intent instead of moving source work.",),
        "verification_plan": ("pytest tests/blueprint/test_effects.py -q",),
        "task_acceptance": ("Blueprint handlers preserve destination-before-source ordering.",),
        "required_checks": ("pytest tests/blueprint/test_effects.py -q",),
        "risk_notes": ("Duplicate promotions must fail.",),
        "references": ("lab/specs/pending/blueprint.md",),
        "created_at": NOW,
    }
    values.update(updates)
    return BlueprintPacketDocument(**values)


def _evaluation(decision: str = "approved", **updates: object) -> BlueprintEvaluationDocument:
    values: dict[str, object] = {
        "evaluation_id": "evaluation-001",
        "blueprint_id": "blueprint-001",
        "draft_id": "draft-001",
        "manifest_id": "manifest-001",
        "root_spec_id": "spec-001",
        "root_idea_id": "idea-001",
        "decision": decision,
        "rubric_findings": ("Blueprint is coherent.",),
        "lineage_consistency_findings": ("Lineage matches active draft.",),
        "verification_findings": ("Checks are concrete.",),
        "required_task_fields": ("task_id", "target_paths") if decision == "approved" else (),
        "critique_id": "critique-001" if decision == "rejected" else None,
        "references": ("lab/specs/pending/blueprint.md",),
        "created_at": NOW,
    }
    values.update(updates)
    return BlueprintEvaluationDocument(**values)


def _critique(**updates: object) -> BlueprintCritiqueDocument:
    values: dict[str, object] = {
        "critique_id": "critique-001",
        "evaluation_id": "evaluation-001",
        "blueprint_id": "blueprint-001",
        "draft_id": "draft-001",
        "manifest_id": "manifest-001",
        "root_spec_id": "spec-001",
        "root_idea_id": "idea-001",
        "revision": 1,
        "required_changes": ("Narrow the implementation sequence.",),
        "verification_issues": ("Add a duplicate promotion check.",),
        "blocking_reason": "Blueprint needs a narrower implementation sequence.",
        "references": ("lab/specs/pending/blueprint.md",),
        "created_at": NOW,
    }
    values.update(updates)
    return BlueprintCritiqueDocument(**values)


def _task(**updates: object) -> TaskDocument:
    values: dict[str, object] = {
        "task_id": "task-001",
        "title": "Implement Blueprint runtime effects",
        "summary": "Add runtime effect handlers for Blueprint Planning.",
        "root_idea_id": "idea-001",
        "root_spec_id": "spec-001",
        "spec_id": "spec-001",
        "target_paths": ("src/millrace_ai/runtime/blueprint_effects.py",),
        "acceptance": ("Blueprint handlers preserve destination-before-source ordering.",),
        "required_checks": ("pytest tests/blueprint/test_effects.py -q",),
        "references": ("lab/specs/pending/blueprint.md",),
        "risk": ("Duplicate promotions must fail.",),
        "created_at": NOW,
        "created_by": "evaluator_blueprint",
    }
    values.update(updates)
    return TaskDocument(**values)


def _approved_task(**updates: object) -> TaskDocument:
    task = _task()
    references = (
        *task.references,
        "millrace-agents/blueprints/packets/approved/blueprint-001.json",
        "millrace-agents/blueprints/evaluations/evaluation-001.json",
    )
    return task.model_copy(update={"references": references, **updates})


def _promotion(**updates: object) -> BlueprintPromotionRecord:
    values: dict[str, object] = {
        "promotion_id": "promotion-evaluation-001",
        "blueprint_id": "blueprint-001",
        "evaluation_id": "evaluation-001",
        "draft_id": "draft-001",
        "manifest_id": "manifest-001",
        "root_spec_id": "spec-001",
        "root_idea_id": "idea-001",
        "generated_task_id": "task-001",
        "generated_task_path": "millrace-agents/tasks/queue/task-001.md",
        "approved_blueprint_path": "millrace-agents/blueprints/packets/approved/blueprint-001.json",
        "evaluation_path": "millrace-agents/blueprints/evaluations/evaluation-001.json",
        "promoted_at": NOW,
    }
    values.update(updates)
    return BlueprintPromotionRecord(**values)


def _activate_blueprint_draft(paths) -> None:
    active_dir = paths.runtime_root / "blueprints/drafts/active"
    active_dir.mkdir(parents=True, exist_ok=True)
    _write_json(
        active_dir / "draft-001.json",
        _draft("draft-001", draft_index=1).model_copy(update={"status": "active"}),
    )


def _write_candidate_outputs(
    run_dir: Path,
    *,
    packet: object | None = None,
    markdown: str = "# Blueprint\n\nImplement runtime effects.\n",
) -> None:
    _write_json(run_dir / "blueprint_packet.json", _packet() if packet is None else packet)
    (run_dir / "blueprint.md").write_text(markdown, encoding="utf-8")


def _candidate_packet_path(paths) -> Path:
    return paths.runtime_root / "blueprints/packets/candidates/blueprint-001.json"


def _candidate_markdown_path(paths) -> Path:
    return paths.runtime_root / "blueprints/packets/candidates/blueprint-001.md"


def _active_draft_path(paths) -> Path:
    return paths.runtime_root / "blueprints/drafts/active/draft-001.json"


def _approved_packet_path(paths) -> Path:
    return paths.runtime_root / "blueprints/packets/approved/blueprint-001.json"


def _approved_markdown_path(paths) -> Path:
    return paths.runtime_root / "blueprints/packets/approved/blueprint-001.md"


def _approved_markdown_checksum_path(paths) -> Path:
    return paths.runtime_root / "blueprints/packets/approved/blueprint-001.md.sha256"


def _rejected_packet_path(paths) -> Path:
    return paths.runtime_root / "blueprints/packets/rejected/blueprint-001.json"


def _rejected_markdown_path(paths) -> Path:
    return paths.runtime_root / "blueprints/packets/rejected/blueprint-001.md"


def _rejected_markdown_checksum_path(paths) -> Path:
    return paths.runtime_root / "blueprints/packets/rejected/blueprint-001.md.sha256"


def _evaluation_path(paths) -> Path:
    return paths.runtime_root / "blueprints/evaluations/evaluation-001.json"


def _critique_path(paths) -> Path:
    return paths.runtime_root / "blueprints/critiques/open/critique-001.json"


def _promotion_path(paths) -> Path:
    return paths.runtime_root / "blueprints/promotions/promotion-evaluation-001.json"


def _task_path(paths) -> Path:
    return paths.tasks_queue_dir / "task-001.md"


def _optional_text(path: Path) -> str | None:
    return path.read_text(encoding="utf-8") if path.exists() else None


def _write_markdown_checksum(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"{effect_operations._normalized_markdown_sha256(content)}\n",
        encoding="utf-8",
    )


def _write_manager_outputs(run_dir: Path, *, manifest: object | None = None, drafts: object | None = None) -> None:
    if manifest is not None:
        _write_json(run_dir / "blueprint_manifest.json", manifest)
    if drafts is not None:
        _write_json(run_dir / "blueprint_drafts.json", drafts)


def _run_manager_case(
    tmp_path: Path,
    runner: ManagerRunner,
    setup,
) -> tuple[RuntimeEffectResult, object]:
    paths = _workspace(tmp_path)
    _activate_source_spec(paths)
    run_dir = _run_dir(tmp_path)
    _write_manager_outputs(run_dir, manifest=_manifest(), drafts=list(_drafts()))
    setup(paths, run_dir)
    result = runner(paths, _stage_result(), run_dir, None)
    return result, paths


def _result_payload(result: RuntimeEffectResult) -> dict[str, object]:
    return result.model_dump(mode="json", exclude={"mutation_journal"})


def _result_payload_without_markdown_checksums(result: RuntimeEffectResult) -> dict[str, object]:
    payload = _result_payload(result)
    payload["created_paths"] = [
        path
        for path in payload.get("created_paths", [])
        if not str(path).endswith(".md.sha256")
    ]
    return payload


def _assert_runner_parity(tmp_path: Path, setup) -> None:
    legacy_result, legacy_paths = _run_manager_case(
        tmp_path / "legacy",
        blueprint_effects._legacy_manager_blueprint_manifest_to_blueprint_drafts,
        setup,
    )
    operation_result, operation_paths = _run_manager_case(
        tmp_path / "operation",
        effect_operations.manager_blueprint_manifest_to_blueprint_drafts,
        setup,
    )

    assert _result_payload_without_markdown_checksums(
        operation_result,
    ) == _result_payload_without_markdown_checksums(legacy_result)
    assert (
        operation_paths.runtime_root / "blueprints/manifests/manifest-001.json"
    ).exists() == (
        legacy_paths.runtime_root / "blueprints/manifests/manifest-001.json"
    ).exists()
    assert (
        operation_paths.runtime_root / "blueprints/drafts/queue/draft-001.json"
    ).exists() == (
        legacy_paths.runtime_root / "blueprints/drafts/queue/draft-001.json"
    ).exists()
    assert (
        operation_paths.runtime_root / "blueprints/drafts/queue/draft-002.json"
    ).exists() == (
        legacy_paths.runtime_root / "blueprints/drafts/queue/draft-002.json"
    ).exists()


def _run_contractor_case(
    tmp_path: Path,
    runner: ContractorRunner,
    setup,
) -> tuple[RuntimeEffectResult, object]:
    paths = _workspace(tmp_path)
    _activate_blueprint_draft(paths)
    run_dir = _run_dir(tmp_path)
    _write_candidate_outputs(run_dir)
    setup(paths, run_dir)
    result = runner(paths, _contractor_stage_result(), run_dir, None)
    return result, paths


def _assert_contractor_parity(tmp_path: Path, setup) -> None:
    legacy_result, legacy_paths = _run_contractor_case(
        tmp_path / "legacy",
        blueprint_effects._legacy_contractor_blueprint_candidate_persist,
        setup,
    )
    operation_result, operation_paths = _run_contractor_case(
        tmp_path / "operation",
        effect_operations.contractor_blueprint_candidate_persist,
        setup,
    )

    assert _result_payload_without_markdown_checksums(
        operation_result,
    ) == _result_payload_without_markdown_checksums(legacy_result)
    assert _candidate_packet_path(operation_paths).exists() == _candidate_packet_path(
        legacy_paths,
    ).exists()
    assert _candidate_markdown_path(operation_paths).exists() == _candidate_markdown_path(
        legacy_paths,
    ).exists()
    assert (
        read_blueprint_draft(_active_draft_path(operation_paths)).model_dump(mode="json")
        == read_blueprint_draft(_active_draft_path(legacy_paths)).model_dump(mode="json")
    )


def _persist_evaluator_candidate_state(paths, run_dir: Path) -> None:
    active_dir = paths.runtime_root / "blueprints/drafts/active"
    active_dir.mkdir(parents=True, exist_ok=True)
    _write_json(
        active_dir / "draft-001.json",
        _draft("draft-001", draft_index=1).model_copy(
            update={"status": "active", "latest_blueprint_id": "blueprint-001"},
        ),
    )
    persist_blueprint_packet(paths, _packet(), packet_state="candidates")
    _candidate_markdown_path(paths).write_text(
        "# Blueprint\n\nImplement runtime effects.\n",
        encoding="utf-8",
    )


def _write_approval_outputs(run_dir: Path, *, task: TaskDocument | None = None) -> None:
    _write_json(run_dir / "blueprint_evaluation.json", _evaluation("approved"))
    (run_dir / "generated_task.md").write_text(
        render_work_document(_task() if task is None else task),
        encoding="utf-8",
    )


def _write_rejection_outputs(run_dir: Path, *, critique: object | None = None) -> None:
    _write_json(run_dir / "blueprint_evaluation.json", _evaluation("rejected"))
    _write_json(run_dir / "blueprint_critique.json", _critique() if critique is None else critique)


def _run_evaluator_approval_case(
    tmp_path: Path,
    runner: EvaluatorRunner,
    setup,
) -> tuple[RuntimeEffectResult, object]:
    paths = _workspace(tmp_path)
    run_dir = _run_dir(tmp_path)
    _persist_evaluator_candidate_state(paths, run_dir)
    _write_approval_outputs(run_dir)
    setup(paths, run_dir)
    result = runner(paths, _evaluator_stage_result("BLUEPRINT_APPROVED"), run_dir, None)
    return result, paths


def _run_evaluator_rejection_case(
    tmp_path: Path,
    runner: EvaluatorRunner,
    setup,
) -> tuple[RuntimeEffectResult, object]:
    paths = _workspace(tmp_path)
    run_dir = _run_dir(tmp_path)
    _persist_evaluator_candidate_state(paths, run_dir)
    _write_rejection_outputs(run_dir)
    setup(paths, run_dir)
    result = runner(paths, _evaluator_stage_result("BLUEPRINT_REJECTED"), run_dir, None)
    return result, paths


def _assert_evaluator_approval_parity(tmp_path: Path, setup) -> None:
    legacy_result, legacy_paths = _run_evaluator_approval_case(
        tmp_path / "legacy",
        blueprint_effects._legacy_evaluator_blueprint_approved_to_task,
        setup,
    )
    operation_result, operation_paths = _run_evaluator_approval_case(
        tmp_path / "operation",
        effect_operations.evaluator_blueprint_approved_to_task,
        setup,
    )

    assert _result_payload_without_markdown_checksums(
        operation_result,
    ) == _result_payload_without_markdown_checksums(legacy_result)
    for operation_path, legacy_path in (
        (_evaluation_path(operation_paths), _evaluation_path(legacy_paths)),
        (_approved_packet_path(operation_paths), _approved_packet_path(legacy_paths)),
        (_approved_markdown_path(operation_paths), _approved_markdown_path(legacy_paths)),
        (_candidate_packet_path(operation_paths), _candidate_packet_path(legacy_paths)),
        (_candidate_markdown_path(operation_paths), _candidate_markdown_path(legacy_paths)),
        (_task_path(operation_paths), _task_path(legacy_paths)),
        (_promotion_path(operation_paths), _promotion_path(legacy_paths)),
    ):
        assert operation_path.exists() == legacy_path.exists()
        assert _optional_text(operation_path) == _optional_text(legacy_path)


def _assert_evaluator_rejection_parity(tmp_path: Path, setup) -> None:
    legacy_result, legacy_paths = _run_evaluator_rejection_case(
        tmp_path / "legacy",
        blueprint_effects._legacy_evaluator_blueprint_rejected_to_draft_revision,
        setup,
    )
    operation_result, operation_paths = _run_evaluator_rejection_case(
        tmp_path / "operation",
        effect_operations.evaluator_blueprint_rejected_to_draft_revision,
        setup,
    )

    assert _result_payload_without_markdown_checksums(
        operation_result,
    ) == _result_payload_without_markdown_checksums(legacy_result)
    for operation_path, legacy_path in (
        (_evaluation_path(operation_paths), _evaluation_path(legacy_paths)),
        (_critique_path(operation_paths), _critique_path(legacy_paths)),
        (_rejected_packet_path(operation_paths), _rejected_packet_path(legacy_paths)),
        (_rejected_markdown_path(operation_paths), _rejected_markdown_path(legacy_paths)),
        (_candidate_packet_path(operation_paths), _candidate_packet_path(legacy_paths)),
        (_candidate_markdown_path(operation_paths), _candidate_markdown_path(legacy_paths)),
    ):
        assert operation_path.exists() == legacy_path.exists()
        assert _optional_text(operation_path) == _optional_text(legacy_path)
    assert (
        read_blueprint_draft(_active_draft_path(operation_paths)).model_dump(mode="json")
        == read_blueprint_draft(_active_draft_path(legacy_paths)).model_dump(mode="json")
    )


def test_manager_operation_matches_legacy_success(tmp_path: Path) -> None:
    _assert_runner_parity(tmp_path, lambda paths, run_dir: None)


def test_manager_operation_matches_legacy_duplicate_equivalent(tmp_path: Path) -> None:
    def setup(paths, run_dir) -> None:
        write_blueprint_manifest(paths, _manifest())
        enqueue_blueprint_draft(paths, _draft("draft-001", draft_index=1))

    _assert_runner_parity(tmp_path, setup)


def test_manager_operation_matches_legacy_duplicate_conflict(tmp_path: Path) -> None:
    def setup(paths, run_dir) -> None:
        write_blueprint_manifest(
            paths,
            _manifest(spec_summary="Already persisted divergent manifest."),
        )

    _assert_runner_parity(tmp_path, setup)


def test_manager_operation_matches_legacy_invalid_schema(tmp_path: Path) -> None:
    def setup(paths, run_dir) -> None:
        _write_json(run_dir / "blueprint_manifest.json", {"manifest_id": "manifest-001"})

    _assert_runner_parity(tmp_path, setup)


def test_manager_operation_matches_legacy_source_lifecycle_invalid(tmp_path: Path) -> None:
    def setup(paths, run_dir) -> None:
        del run_dir
        (paths.specs_active_dir / "spec-001.md").unlink()

    _assert_runner_parity(tmp_path, setup)


def test_manager_operation_matches_legacy_partial_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_legacy_enqueue = blueprint_effects.enqueue_blueprint_draft
    original_operation_enqueue = effect_operations.enqueue_blueprint_draft

    def fail_second_enqueue(paths, draft):
        if draft.draft_id == "draft-002":
            raise OSError("simulated write failure")
        return original_legacy_enqueue(paths, draft)

    def fail_second_operation_enqueue(paths, draft):
        if draft.draft_id == "draft-002":
            raise OSError("simulated write failure")
        return original_operation_enqueue(paths, draft)

    monkeypatch.setattr(blueprint_effects, "enqueue_blueprint_draft", fail_second_enqueue)
    monkeypatch.setattr(
        effect_operations,
        "enqueue_blueprint_draft",
        fail_second_operation_enqueue,
    )

    _assert_runner_parity(tmp_path, lambda paths, run_dir: None)


def test_manager_operation_records_mutation_journal_for_durable_writes(
    tmp_path: Path,
) -> None:
    result, _paths = _run_manager_case(
        tmp_path,
        effect_operations.manager_blueprint_manifest_to_blueprint_drafts,
        lambda paths, run_dir: None,
    )

    assert result.mutation_journal == (
        {
            "operation_id": "manager_blueprint_manifest_to_blueprint_drafts",
            "rule_id": "manager_blueprint_manifest_to_blueprint_drafts",
            "run_id": "run-manager-001",
            "step_id": "persist_manifest",
            "mutation_phase": "partial_mutation",
            "created_path": "millrace-agents/blueprints/manifests/manifest-001.json",
        },
        {
            "operation_id": "manager_blueprint_manifest_to_blueprint_drafts",
            "rule_id": "manager_blueprint_manifest_to_blueprint_drafts",
            "run_id": "run-manager-001",
            "step_id": "enqueue_drafts",
            "mutation_phase": "partial_mutation",
            "created_path": "millrace-agents/blueprints/drafts/queue/draft-001.json",
            "work_item_id": "draft-001",
        },
        {
            "operation_id": "manager_blueprint_manifest_to_blueprint_drafts",
            "rule_id": "manager_blueprint_manifest_to_blueprint_drafts",
            "run_id": "run-manager-001",
            "step_id": "enqueue_drafts",
            "mutation_phase": "partial_mutation",
            "created_path": "millrace-agents/blueprints/drafts/queue/draft-002.json",
            "work_item_id": "draft-002",
        },
        {
            "operation_id": "manager_blueprint_manifest_to_blueprint_drafts",
            "rule_id": "manager_blueprint_manifest_to_blueprint_drafts",
            "run_id": "run-manager-001",
            "step_id": "complete_source_lifecycle",
            "mutation_phase": "partial_mutation",
            "source_lifecycle_action": "complete",
            "work_item_family_id": "spec",
            "work_item_kind": "spec",
            "work_item_id": "spec-001",
        },
    )


def test_contractor_operation_matches_legacy_success(tmp_path: Path) -> None:
    _assert_contractor_parity(tmp_path, lambda paths, run_dir: None)


def test_contractor_operation_matches_legacy_replay_equivalent_outputs(
    tmp_path: Path,
) -> None:
    def setup(paths, run_dir) -> None:
        persist_blueprint_packet(paths, _packet(), packet_state="candidates")
        markdown_path = _candidate_markdown_path(paths)
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_path.write_text(
            "# Blueprint\n\nImplement runtime effects.\n",
            encoding="utf-8",
        )

    _assert_contractor_parity(tmp_path, setup)


def test_contractor_operation_matches_legacy_duplicate_conflict(tmp_path: Path) -> None:
    def setup(paths, run_dir) -> None:
        persist_blueprint_packet(
            paths,
            _packet(title="Divergent candidate"),
            packet_state="candidates",
        )

    _assert_contractor_parity(tmp_path, setup)


def test_contractor_operation_matches_legacy_markdown_mismatch(tmp_path: Path) -> None:
    def setup(paths, run_dir) -> None:
        persist_blueprint_packet(paths, _packet(), packet_state="candidates")
        markdown_path = _candidate_markdown_path(paths)
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_path.write_text(
            "# Blueprint\n\nDifferent contractor output.\n",
            encoding="utf-8",
        )

    _assert_contractor_parity(tmp_path, setup)


def test_contractor_operation_preserves_active_draft_update_fields(
    tmp_path: Path,
) -> None:
    result, paths = _run_contractor_case(
        tmp_path,
        effect_operations.contractor_blueprint_candidate_persist,
        lambda paths, run_dir: None,
    )

    active_draft = read_blueprint_draft(_active_draft_path(paths))
    assert result.decision is RuntimeEffectDecision.CONTINUE_ROUTE
    assert active_draft.status == "active"
    assert active_draft.latest_blueprint_id == "blueprint-001"
    assert active_draft.current_revision == 0
    assert active_draft.updated_at is None


def test_contractor_operation_records_mutation_journal_for_durable_writes(
    tmp_path: Path,
) -> None:
    result, _paths = _run_contractor_case(
        tmp_path,
        effect_operations.contractor_blueprint_candidate_persist,
        lambda paths, run_dir: None,
    )

    assert result.mutation_journal == (
        {
            "operation_id": "contractor_blueprint_candidate_persist",
            "rule_id": "contractor_blueprint_candidate_persist",
            "run_id": "run-contractor-001",
            "step_id": "persist_candidate_packet",
            "mutation_phase": "partial_mutation",
            "blueprint_id": "blueprint-001",
            "created_path": "millrace-agents/blueprints/packets/candidates/blueprint-001.json",
        },
        {
            "operation_id": "contractor_blueprint_candidate_persist",
            "rule_id": "contractor_blueprint_candidate_persist",
            "run_id": "run-contractor-001",
            "step_id": "copy_candidate_markdown",
            "mutation_phase": "partial_mutation",
            "blueprint_id": "blueprint-001",
            "created_path": "millrace-agents/blueprints/packets/candidates/blueprint-001.md",
        },
        {
            "operation_id": "contractor_blueprint_candidate_persist",
            "rule_id": "contractor_blueprint_candidate_persist",
            "run_id": "run-contractor-001",
            "step_id": "update_active_draft",
            "mutation_phase": "partial_mutation",
            "blueprint_id": "blueprint-001",
            "updated_path": "millrace-agents/blueprints/drafts/active/draft-001.json",
            "work_item_id": "draft-001",
        },
    )


def test_evaluator_approval_operation_matches_legacy_success(tmp_path: Path) -> None:
    _assert_evaluator_approval_parity(tmp_path, lambda paths, run_dir: None)


def test_evaluator_approval_operation_replays_completed_approved_state(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    run_dir = _run_dir(tmp_path)
    _persist_evaluator_candidate_state(paths, run_dir)
    _write_approval_outputs(run_dir)

    first = effect_operations.evaluator_blueprint_approved_to_task(
        paths,
        _evaluator_stage_result("BLUEPRINT_APPROVED"),
        run_dir,
        None,
    )
    active_path = _active_draft_path(paths)
    approved_draft_path = paths.runtime_root / "blueprints/drafts/approved/draft-001.json"
    approved_draft_path.parent.mkdir(parents=True, exist_ok=True)
    active_path.replace(approved_draft_path)

    replay = effect_operations.evaluator_blueprint_approved_to_task(
        paths,
        _evaluator_stage_result("BLUEPRINT_APPROVED"),
        run_dir,
        None,
    )

    assert first.decision is RuntimeEffectDecision.REQUEST_COMPLETE_SOURCE
    assert replay.decision is RuntimeEffectDecision.REQUEST_COMPLETE_SOURCE
    assert replay.failure_class is None
    assert replay.created_paths == ()
    assert replay.source_lifecycle_intent is None
    assert _approved_markdown_path(paths).is_file()
    assert _approved_markdown_checksum_path(paths).is_file()
    assert not _candidate_markdown_path(paths).exists()


def test_evaluator_approval_operation_blocks_replay_with_modified_approved_markdown(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    run_dir = _run_dir(tmp_path)
    _persist_evaluator_candidate_state(paths, run_dir)
    _write_approval_outputs(run_dir)
    first = effect_operations.evaluator_blueprint_approved_to_task(
        paths,
        _evaluator_stage_result("BLUEPRINT_APPROVED"),
        run_dir,
        None,
    )
    active_path = _active_draft_path(paths)
    approved_draft_path = paths.runtime_root / "blueprints/drafts/approved/draft-001.json"
    approved_draft_path.parent.mkdir(parents=True, exist_ok=True)
    active_path.replace(approved_draft_path)
    _approved_markdown_path(paths).write_text(
        "# Blueprint\n\nModified after approval.\n",
        encoding="utf-8",
    )

    replay = effect_operations.evaluator_blueprint_approved_to_task(
        paths,
        _evaluator_stage_result("BLUEPRINT_APPROVED"),
        run_dir,
        None,
    )

    assert first.decision is RuntimeEffectDecision.REQUEST_COMPLETE_SOURCE
    assert replay.decision is RuntimeEffectDecision.REQUEST_BLOCK_SOURCE
    assert replay.failure_class == "blueprint_approved_markdown_conflict"


def test_evaluator_approval_operation_matches_legacy_equivalent_pre_lifecycle_state(
    tmp_path: Path,
) -> None:
    def setup(paths, run_dir) -> None:
        (run_dir / "blueprint.md").write_text(
            "# Blueprint\n\nImplement runtime effects.\n",
            encoding="utf-8",
        )
        persist_blueprint_evaluation(paths, _evaluation("approved"))
        _approved_packet_path(paths).parent.mkdir(parents=True, exist_ok=True)
        _candidate_packet_path(paths).replace(_approved_packet_path(paths))
        _approved_markdown_path(paths).parent.mkdir(parents=True, exist_ok=True)
        _candidate_markdown_path(paths).replace(_approved_markdown_path(paths))
        enqueue_task(paths, _approved_task())
        persist_blueprint_promotion(paths, _promotion())

    _assert_evaluator_approval_parity(tmp_path, setup)


def test_evaluator_approval_operation_matches_legacy_invalid_generated_task(
    tmp_path: Path,
) -> None:
    def setup(paths, run_dir) -> None:
        _write_approval_outputs(
            run_dir,
            task=_task(target_paths=("src/millrace_ai/runtime/unowned.py",)),
        )

    _assert_evaluator_approval_parity(tmp_path, setup)


def test_evaluator_approval_operation_matches_legacy_partial_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_legacy_enqueue = blueprint_effects.enqueue_task
    original_operation_enqueue = effect_operations.enqueue_task

    def fail_legacy_enqueue(paths, task):
        raise OSError("simulated task enqueue failure")

    def fail_operation_enqueue(paths, task):
        raise OSError("simulated task enqueue failure")

    assert original_legacy_enqueue is not None
    assert original_operation_enqueue is not None
    monkeypatch.setattr(blueprint_effects, "enqueue_task", fail_legacy_enqueue)
    monkeypatch.setattr(effect_operations, "enqueue_task", fail_operation_enqueue)

    _assert_evaluator_approval_parity(tmp_path, lambda paths, run_dir: None)


def test_evaluator_rejection_operation_matches_legacy_success(tmp_path: Path) -> None:
    _assert_evaluator_rejection_parity(tmp_path, lambda paths, run_dir: None)


def test_evaluator_rejection_operation_replays_completed_rejection_state(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    run_dir = _run_dir(tmp_path)
    _persist_evaluator_candidate_state(paths, run_dir)
    _write_rejection_outputs(run_dir)

    first = effect_operations.evaluator_blueprint_rejected_to_draft_revision(
        paths,
        _evaluator_stage_result("BLUEPRINT_REJECTED"),
        run_dir,
        None,
    )
    second = effect_operations.evaluator_blueprint_rejected_to_draft_revision(
        paths,
        _evaluator_stage_result("BLUEPRINT_REJECTED"),
        run_dir,
        None,
    )

    assert first.decision is RuntimeEffectDecision.CONTINUE_ROUTE
    assert second.decision is RuntimeEffectDecision.CONTINUE_ROUTE
    assert second.failure_class is None
    assert second.created_paths == ()
    assert second.mutation_journal == ()
    assert _critique_path(paths).is_file()
    assert _rejected_packet_path(paths).is_file()
    assert _rejected_markdown_checksum_path(paths).is_file()
    assert not _candidate_packet_path(paths).exists()


def test_evaluator_rejection_operation_blocks_replay_with_modified_rejected_markdown(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    run_dir = _run_dir(tmp_path)
    _persist_evaluator_candidate_state(paths, run_dir)
    _write_rejection_outputs(run_dir)
    first = effect_operations.evaluator_blueprint_rejected_to_draft_revision(
        paths,
        _evaluator_stage_result("BLUEPRINT_REJECTED"),
        run_dir,
        None,
    )
    _rejected_markdown_path(paths).write_text("", encoding="utf-8")

    replay = effect_operations.evaluator_blueprint_rejected_to_draft_revision(
        paths,
        _evaluator_stage_result("BLUEPRINT_REJECTED"),
        run_dir,
        None,
    )

    assert first.decision is RuntimeEffectDecision.CONTINUE_ROUTE
    assert replay.decision is RuntimeEffectDecision.REQUEST_BLOCK_SOURCE
    assert replay.failure_class == "blueprint_rejection_duplicate_conflict"


def test_evaluator_rejection_operation_matches_legacy_invalid_critique(
    tmp_path: Path,
) -> None:
    def setup(paths, run_dir) -> None:
        (run_dir / "blueprint_critique.json").write_text(
            '{"critique_id": "critique-001", "required_changes": 12}',
            encoding="utf-8",
        )

    _assert_evaluator_rejection_parity(tmp_path, setup)


def test_evaluator_rejection_operation_matches_legacy_partial_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_legacy_critique(paths, critique, *, critique_state="open"):
        raise OSError("simulated critique persistence failure")

    def fail_operation_critique(paths, critique, *, critique_state="open"):
        raise OSError("simulated critique persistence failure")

    monkeypatch.setattr(
        blueprint_effects,
        "persist_blueprint_critique",
        fail_legacy_critique,
    )
    monkeypatch.setattr(
        effect_operations,
        "persist_blueprint_critique",
        fail_operation_critique,
    )

    _assert_evaluator_rejection_parity(tmp_path, lambda paths, run_dir: None)
