from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from millrace_ai.contracts import (
    BlueprintCritiqueDocument,
    BlueprintDraftDocument,
    BlueprintEvaluationDocument,
    BlueprintManifestDocument,
    BlueprintPacketDocument,
    BlueprintPromotionRecord,
)

NOW = datetime(2026, 4, 15, 12, 0, 0, tzinfo=timezone.utc)


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
        strict_sequence=True,
        spec_summary="Build a configurable workflow loop.",
        decomposition_strategy="Split by durable runtime boundary.",
        global_acceptance_intent=("workflow config remains compiler validated",),
        integration_boundary_notes=("do not contaminate standard modes",),
        risk_notes=("state drift",),
        references=("lab/specs/pending/blueprint.md",),
        created_at=NOW,
    )


def _draft(
    draft_id: str = "draft-001",
    *,
    depends_on_draft_ids: tuple[str, ...] = (),
    draft_index: int = 1,
    current_revision: int = 0,
    status: str = "queued",
) -> BlueprintDraftDocument:
    return BlueprintDraftDocument(
        draft_id=draft_id,
        manifest_id="manifest-001",
        root_spec_id="spec-001",
        root_idea_id="idea-001",
        source_spec_id="spec-001",
        draft_index=draft_index,
        depends_on_draft_ids=depends_on_draft_ids,
        title="Add workflow models",
        summary="Create typed contracts",
        scope=("src/millrace_ai/contracts/blueprint.py",),
        non_goals=("runtime effects",),
        target_paths=("src/millrace_ai/contracts/blueprint.py",),
        acceptance_intent=("contracts validate coherent packets",),
        verification_intent=("pytest tests/blueprint/test_contracts.py -q",),
        dependency_notes=("none",),
        integration_boundary_notes=("no source mutation",),
        context_excerpt="Only the contract package is in scope.",
        current_revision=current_revision,
        status=status,
        references=("lab/specs/pending/blueprint.md",),
        created_at=NOW,
    )


def _packet(
    blueprint_id: str = "blueprint-001",
    *,
    draft_id: str = "draft-001",
    revision: int = 1,
) -> BlueprintPacketDocument:
    return BlueprintPacketDocument(
        blueprint_id=blueprint_id,
        draft_id=draft_id,
        manifest_id="manifest-001",
        root_spec_id="spec-001",
        root_idea_id="idea-001",
        revision=revision,
        title="Implement Blueprint contracts",
        implementation_scope=("Add typed Blueprint models.",),
        intended_files=("src/millrace_ai/contracts/blueprint.py",),
        design_decisions=("Use closed Pydantic contracts.",),
        non_goals=("runtime effects",),
        dependency_assumptions=("draft-000 already approved",),
        verification_plan=("pytest tests/blueprint/test_contracts.py -q",),
        task_acceptance=("Blueprint documents reject incoherent ids.",),
        required_checks=("pytest tests/blueprint/test_contracts.py -q",),
        risk_notes=("contract drift",),
        open_questions=(),
        references=("lab/specs/pending/blueprint.md",),
        created_at=NOW,
    )


def _evaluation(
    decision: str = "approved",
    *,
    blueprint_id: str = "blueprint-001",
    critique_id: str | None = None,
) -> BlueprintEvaluationDocument:
    return BlueprintEvaluationDocument(
        evaluation_id="evaluation-001",
        blueprint_id=blueprint_id,
        draft_id="draft-001",
        manifest_id="manifest-001",
        root_spec_id="spec-001",
        root_idea_id="idea-001",
        decision=decision,
        rubric_findings=("scope is bounded",),
        lineage_consistency_findings=("lineage matches manifest",),
        dependency_findings=("dependencies are explicit",),
        verification_findings=("checks are concrete",),
        overlap_findings=("no duplicate scope",),
        required_task_fields=("task_id", "target_paths") if decision == "approved" else (),
        critique_id=critique_id,
        references=("lab/specs/pending/blueprint.md",),
        created_at=NOW,
    )


def test_blueprint_contracts_validate_coherent_documents() -> None:
    manifest = _manifest()
    draft = _draft()
    packet = _packet()
    evaluation = _evaluation()
    promotion = BlueprintPromotionRecord(
        promotion_id="promotion-001",
        blueprint_id=packet.blueprint_id,
        evaluation_id=evaluation.evaluation_id,
        draft_id=draft.draft_id,
        manifest_id=manifest.manifest_id,
        root_spec_id=manifest.root_spec_id,
        root_idea_id=manifest.root_idea_id,
        generated_task_id="task-001",
        generated_task_path="millrace-agents/tasks/queue/task-001.md",
        approved_blueprint_path="millrace-agents/blueprints/packets/approved/blueprint-001.json",
        evaluation_path="millrace-agents/blueprints/evaluations/evaluation-001.json",
        promoted_at=NOW,
    )

    assert manifest.kind == "blueprint_manifest"
    assert draft.kind == "blueprint_draft"
    assert packet.kind == "blueprint_packet"
    assert evaluation.kind == "blueprint_evaluation"
    assert promotion.kind == "blueprint_promotion"
    packet.ensure_matches_draft(draft)
    evaluation.ensure_matches_packet(packet)
    promotion.ensure_matches_evaluation(evaluation)


def test_blueprint_models_reject_bad_ids_and_negative_revisions() -> None:
    with pytest.raises(ValidationError, match="draft_id"):
        _draft("../escape")
    with pytest.raises(ValidationError, match="current_revision"):
        _draft(current_revision=-1)
    with pytest.raises(ValidationError, match="revision"):
        _packet(revision=0)


def test_manifest_requires_strict_unique_draft_ids_and_count() -> None:
    with pytest.raises(ValidationError, match="draft_count"):
        BlueprintManifestDocument(
            **(_manifest().model_dump(mode="python") | {"draft_count": 3})
        )
    with pytest.raises(ValidationError, match="draft_ids"):
        BlueprintManifestDocument(
            **(
                _manifest().model_dump(mode="python")
                | {"draft_ids": ("draft-001", "draft-001")}
            )
        )
    with pytest.raises(ValidationError, match="strict_sequence"):
        BlueprintManifestDocument(
            **(_manifest().model_dump(mode="python") | {"strict_sequence": False})
        )


def test_draft_rejects_self_dependency_and_invalid_status() -> None:
    with pytest.raises(ValidationError, match="depends_on_draft_ids"):
        _draft(depends_on_draft_ids=("draft-001",))
    with pytest.raises(ValidationError):
        _draft(status="done")


def test_packet_requires_required_artifacts_and_matching_draft_refs() -> None:
    with pytest.raises(ValidationError, match="implementation_scope"):
        BlueprintPacketDocument(
            **(_packet().model_dump(mode="python") | {"implementation_scope": ()})
        )

    packet = _packet(draft_id="draft-002")
    with pytest.raises(ValueError, match="draft_id"):
        packet.ensure_matches_draft(_draft("draft-001"))


def test_critique_requires_issue_and_resolution_pairing() -> None:
    critique = BlueprintCritiqueDocument(
        critique_id="critique-001",
        evaluation_id="evaluation-001",
        blueprint_id="blueprint-001",
        draft_id="draft-001",
        manifest_id="manifest-001",
        root_spec_id="spec-001",
        root_idea_id="idea-001",
        revision=1,
        required_changes=("Narrow the file scope.",),
        blocking_reason="scope is too broad",
        references=("lab/specs/pending/blueprint.md",),
        created_at=NOW,
    )

    assert critique.kind == "blueprint_critique"
    with pytest.raises(ValidationError, match="issue"):
        BlueprintCritiqueDocument(
            **(
                critique.model_dump(mode="python")
                | {"required_changes": (), "blocking_reason": "missing issues"}
            )
        )
    with pytest.raises(ValidationError, match="resolved"):
        BlueprintCritiqueDocument(
            **(critique.model_dump(mode="python") | {"resolved_by_blueprint_id": "blueprint-002"})
        )


def test_evaluation_decision_requires_task_fields_or_critique() -> None:
    with pytest.raises(ValidationError, match="required_task_fields"):
        BlueprintEvaluationDocument(
            **(_evaluation().model_dump(mode="python") | {"required_task_fields": ()})
        )
    with pytest.raises(ValidationError, match="critique_id"):
        _evaluation(decision="rejected")


def test_promotion_record_rejects_wrong_artifact_destinations() -> None:
    with pytest.raises(ValidationError, match="generated_task_path"):
        BlueprintPromotionRecord(
            promotion_id="promotion-001",
            blueprint_id="blueprint-001",
            evaluation_id="evaluation-001",
            draft_id="draft-001",
            manifest_id="manifest-001",
            root_spec_id="spec-001",
            root_idea_id="idea-001",
            generated_task_id="task-001",
            generated_task_path="millrace-agents/tasks/done/task-001.md",
            approved_blueprint_path="millrace-agents/blueprints/packets/approved/blueprint-001.json",
            evaluation_path="millrace-agents/blueprints/evaluations/evaluation-001.json",
            promoted_at=NOW,
        )
