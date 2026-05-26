from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import BaseModel

from millrace_ai.contracts import (
    BlueprintDraftDocument,
    BlueprintManifestDocument,
    Plane,
    ResultClass,
    SpecDocument,
    StageResultEnvelope,
    WorkItemKind,
)
from millrace_ai.paths import bootstrap_workspace, workspace_paths
from millrace_ai.runtime import blueprint_effects
from millrace_ai.runtime.effects import RuntimeEffectResult
from millrace_ai.runtime.effects import operations as effect_operations
from millrace_ai.workspace.blueprint_state import enqueue_blueprint_draft, write_blueprint_manifest
from millrace_ai.workspace.work_documents import render_work_document

NOW = datetime(2026, 5, 26, tzinfo=UTC)
ManagerRunner = Callable[[object, StageResultEnvelope, Path, object | None], RuntimeEffectResult]


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

    assert _result_payload(operation_result) == _result_payload(legacy_result)
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
