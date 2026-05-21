from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

import millrace_ai.workspace.blueprint_state as blueprint_state
from millrace_ai.contracts import (
    BlueprintCritiqueDocument,
    BlueprintDraftDocument,
    BlueprintEvaluationDocument,
    BlueprintManifestDocument,
    BlueprintPacketDocument,
    BlueprintPromotionRecord,
    WorkItemKind,
)
from millrace_ai.errors import QueueStateError
from millrace_ai.paths import bootstrap_workspace, workspace_paths
from millrace_ai.workspace.blueprint_state import (
    approve_active_blueprint_draft,
    block_active_blueprint_draft,
    blueprint_artifact_ref,
    cancel_blueprint_draft,
    claim_next_blueprint_draft,
    enqueue_blueprint_draft,
    persist_blueprint_critique,
    persist_blueprint_evaluation,
    persist_blueprint_packet,
    persist_blueprint_promotion,
    read_blueprint_draft,
    requeue_active_blueprint_draft,
    write_blueprint_manifest,
)

NOW = datetime(2026, 4, 15, 12, 0, 0, tzinfo=timezone.utc)


def _workspace(tmp_path: Path):
    return bootstrap_workspace(workspace_paths(tmp_path / "workspace"))


def _manifest(
    manifest_id: str = "manifest-001",
    *,
    root_spec_id: str = "spec-001",
    root_idea_id: str = "idea-001",
    draft_ids: tuple[str, ...] = ("draft-001", "draft-002"),
    spec_summary: str = "Blueprint state helpers",
) -> BlueprintManifestDocument:
    return BlueprintManifestDocument(
        manifest_id=manifest_id,
        root_spec_id=root_spec_id,
        root_idea_id=root_idea_id,
        source_work_item_kind="spec",
        source_work_item_id=root_spec_id,
        source_spec_id=root_spec_id,
        draft_ids=draft_ids,
        draft_count=len(draft_ids),
        strict_sequence=True,
        spec_summary=spec_summary,
        decomposition_strategy="Strict sequence",
        global_acceptance_intent=("drafts claim in order",),
        references=("lab/specs/pending/blueprint.md",),
        created_at=NOW,
    )


def _draft(
    draft_id: str,
    *,
    draft_index: int = 1,
    depends_on_draft_ids: tuple[str, ...] = (),
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
        summary="Queue-managed Blueprint draft.",
        scope=("src/millrace_ai/workspace/blueprint_state.py",),
        target_paths=("src/millrace_ai/workspace/blueprint_state.py",),
        acceptance_intent=("draft lifecycle is deterministic",),
        verification_intent=("pytest tests/blueprint/test_state.py -q",),
        context_excerpt="Only Blueprint state helpers are relevant.",
        current_revision=0,
        references=("lab/specs/pending/blueprint.md",),
        created_at=NOW,
    )


def _write_manifest_file(path: Path, manifest: BlueprintManifestDocument) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _packet() -> BlueprintPacketDocument:
    return BlueprintPacketDocument(
        blueprint_id="blueprint-001",
        draft_id="draft-001",
        manifest_id="manifest-001",
        root_spec_id="spec-001",
        root_idea_id="idea-001",
        revision=1,
        title="Implement state helpers",
        implementation_scope=("Add Blueprint state helpers.",),
        intended_files=("src/millrace_ai/workspace/blueprint_state.py",),
        design_decisions=("Persist JSON contracts.",),
        verification_plan=("pytest tests/blueprint/test_state.py -q",),
        task_acceptance=("state helpers pass tests",),
        required_checks=("pytest tests/blueprint/test_state.py -q",),
        risk_notes=("state drift",),
        references=("lab/specs/pending/blueprint.md",),
        created_at=NOW,
    )


def _evaluation() -> BlueprintEvaluationDocument:
    return BlueprintEvaluationDocument(
        evaluation_id="evaluation-001",
        blueprint_id="blueprint-001",
        draft_id="draft-001",
        manifest_id="manifest-001",
        root_spec_id="spec-001",
        root_idea_id="idea-001",
        decision="approved",
        rubric_findings=("scope is coherent",),
        required_task_fields=("task_id",),
        references=("lab/specs/pending/blueprint.md",),
        created_at=NOW,
    )


def test_write_blueprint_manifest_uses_manifest_id_and_allows_same_root(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    first = _manifest()
    second = _manifest(
        "manifest-002",
        draft_ids=("draft-003",),
        spec_summary="Follow-up Blueprint remediation.",
    )

    first_path = write_blueprint_manifest(paths, first)
    second_path = write_blueprint_manifest(paths, second)

    assert blueprint_state.blueprint_manifest_path(paths, "manifest-001") == first_path
    assert first_path == paths.runtime_root / "blueprints/manifests/manifest-001.json"
    assert second_path == paths.runtime_root / "blueprints/manifests/manifest-002.json"
    assert not (paths.runtime_root / "blueprints/manifests/spec-001.json").exists()
    assert blueprint_state.read_blueprint_manifest(paths, "manifest-001") == first
    assert blueprint_state.read_blueprint_manifest(paths, "manifest-002") == second
    assert tuple(
        manifest.manifest_id
        for manifest in blueprint_state.list_blueprint_manifests_for_root(
            paths,
            "spec-001",
        )
    ) == ("manifest-001", "manifest-002")


def test_read_blueprint_manifest_resolves_legacy_root_keyed_file_by_embedded_id(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    manifest = _manifest(
        "manifest-legacy",
        root_spec_id="spec-root",
        draft_ids=("draft-legacy",),
    )
    _write_manifest_file(
        paths.runtime_root / "blueprints/manifests/spec-root.json",
        manifest,
    )

    assert blueprint_state.read_blueprint_manifest(paths, "manifest-legacy") == manifest
    assert blueprint_state.list_blueprint_manifests(paths) == (manifest,)
    assert blueprint_state.list_blueprint_manifests_for_root(paths, "spec-root") == (manifest,)


def test_read_blueprint_manifest_rejects_divergent_duplicate_manifest_id(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    first = _manifest("manifest-duplicate", root_spec_id="spec-001")
    second = _manifest(
        "manifest-duplicate",
        root_spec_id="spec-002",
        root_idea_id="idea-002",
        draft_ids=("draft-duplicate",),
        spec_summary="Divergent duplicate manifest.",
    )
    _write_manifest_file(
        paths.runtime_root / "blueprints/manifests/manifest-duplicate.json",
        first,
    )
    _write_manifest_file(
        paths.runtime_root / "blueprints/manifests/spec-002.json",
        second,
    )

    with pytest.raises(QueueStateError, match="blueprint_manifest_duplicate"):
        blueprint_state.read_blueprint_manifest(paths, "manifest-duplicate")


def test_read_blueprint_manifest_tolerates_model_equivalent_duplicate_manifest_id(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    manifest = _manifest("manifest-duplicate", root_spec_id="spec-001")
    _write_manifest_file(
        paths.runtime_root / "blueprints/manifests/manifest-duplicate.json",
        manifest,
    )
    _write_manifest_file(
        paths.runtime_root / "blueprints/manifests/spec-001.json",
        manifest,
    )

    assert blueprint_state.read_blueprint_manifest(paths, "manifest-duplicate") == manifest
    assert blueprint_state.list_blueprint_manifests(paths) == (manifest,)


def test_read_blueprint_manifest_missing_id_raises_precise_state_error(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)

    with pytest.raises(QueueStateError, match="blueprint_manifest_missing"):
        blueprint_state.read_blueprint_manifest(paths, "manifest-missing")


def test_claim_next_blueprint_draft_respects_sequence_dependencies(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    enqueue_blueprint_draft(paths, _draft("draft-001", draft_index=1))
    enqueue_blueprint_draft(
        paths,
        _draft("draft-002", draft_index=2, depends_on_draft_ids=("draft-001",)),
    )

    first = claim_next_blueprint_draft(paths, root_spec_id="spec-001")
    assert first is not None
    assert first.work_item_kind is WorkItemKind.BLUEPRINT_DRAFT
    assert first.work_item_id == "draft-001"
    assert read_blueprint_draft(first.path).status == "active"
    assert claim_next_blueprint_draft(paths, root_spec_id="spec-001") is None

    approve_active_blueprint_draft(paths, "draft-001")
    second = claim_next_blueprint_draft(paths, root_spec_id="spec-001")
    assert second is not None
    assert second.work_item_id == "draft-002"


def test_blueprint_draft_lifecycle_requeue_block_and_cancel(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    queue_path = enqueue_blueprint_draft(paths, _draft("draft-001"))
    assert queue_path.name == "draft-001.json"
    with pytest.raises(QueueStateError, match="already exists"):
        enqueue_blueprint_draft(paths, _draft("draft-001"))

    claim = claim_next_blueprint_draft(paths)
    assert claim is not None
    requeued = requeue_active_blueprint_draft(paths, "draft-001")
    assert read_blueprint_draft(requeued).status == "queued"

    claim_next_blueprint_draft(paths)
    blocked = block_active_blueprint_draft(paths, "draft-001")
    assert read_blueprint_draft(blocked).status == "blocked"

    enqueue_blueprint_draft(paths, _draft("draft-002", draft_index=2))
    canceled = cancel_blueprint_draft(paths, "draft-002")
    assert read_blueprint_draft(canceled).status == "canceled"


def test_blueprint_artifacts_persist_with_deterministic_refs(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    manifest_path = write_blueprint_manifest(paths, _manifest())
    packet_path = persist_blueprint_packet(paths, _packet(), packet_state="candidates")
    evaluation_path = persist_blueprint_evaluation(paths, _evaluation())
    critique_path = persist_blueprint_critique(
        paths,
        BlueprintCritiqueDocument(
            critique_id="critique-001",
            evaluation_id="evaluation-002",
            blueprint_id="blueprint-001",
            draft_id="draft-001",
            manifest_id="manifest-001",
            root_spec_id="spec-001",
            root_idea_id="idea-001",
            revision=1,
            required_changes=("Narrow target files.",),
            blocking_reason="scope too broad",
            references=("lab/specs/pending/blueprint.md",),
            created_at=NOW,
        ),
    )
    promotion_path = persist_blueprint_promotion(
        paths,
        BlueprintPromotionRecord(
            promotion_id="promotion-001",
            blueprint_id="blueprint-001",
            evaluation_id="evaluation-001",
            draft_id="draft-001",
            manifest_id="manifest-001",
            root_spec_id="spec-001",
            root_idea_id="idea-001",
            generated_task_id="task-001",
            generated_task_path="millrace-agents/tasks/queue/task-001.md",
            approved_blueprint_path="millrace-agents/blueprints/packets/approved/blueprint-001.json",
            evaluation_path="millrace-agents/blueprints/evaluations/evaluation-001.json",
            promoted_at=NOW,
        ),
    )

    assert blueprint_artifact_ref(paths, manifest_path) == "blueprints/manifests/manifest-001.json"
    assert blueprint_artifact_ref(paths, packet_path) == "blueprints/packets/candidates/blueprint-001.json"
    assert blueprint_artifact_ref(paths, evaluation_path) == "blueprints/evaluations/evaluation-001.json"
    assert blueprint_artifact_ref(paths, critique_path) == "blueprints/critiques/open/critique-001.json"
    assert blueprint_artifact_ref(paths, promotion_path) == "blueprints/promotions/promotion-001.json"

    with pytest.raises(QueueStateError, match="already exists"):
        persist_blueprint_packet(paths, _packet(), packet_state="candidates")
