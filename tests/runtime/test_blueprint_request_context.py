from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from millrace_ai.assets import discover_artifact_contract_definitions
from millrace_ai.contracts import (
    BlueprintCritiqueDocument,
    BlueprintDraftDocument,
    BlueprintEvaluationDocument,
    BlueprintManifestDocument,
    BlueprintPacketDocument,
    Plane,
    PlanningStageName,
    PlanningTerminalResult,
    ResultClass,
    StageResultEnvelope,
    WorkItemKind,
)
from millrace_ai.paths import bootstrap_workspace, workspace_paths
from millrace_ai.runners import StageRunRequest
from millrace_ai.runtime.request_context import attach_default_request_context
from millrace_ai.workspace.blueprint_state import (
    approve_active_blueprint_draft,
    claim_next_blueprint_draft,
    enqueue_blueprint_draft,
    persist_blueprint_critique,
    persist_blueprint_evaluation,
    persist_blueprint_packet,
    write_blueprint_manifest,
)

NOW = datetime(2026, 5, 19, 12, 0, 0, tzinfo=timezone.utc)


def _workspace(tmp_path: Path):
    return bootstrap_workspace(workspace_paths(tmp_path / "workspace"))


def _manifest(
    *,
    manifest_id: str = "manifest-001",
    root_spec_id: str = "spec-001",
    draft_ids: tuple[str, ...] = ("draft-001",),
) -> BlueprintManifestDocument:
    return BlueprintManifestDocument(
        manifest_id=manifest_id,
        root_spec_id=root_spec_id,
        root_idea_id="idea-001",
        source_work_item_kind="spec",
        source_work_item_id=root_spec_id,
        source_spec_id=root_spec_id,
        draft_ids=draft_ids,
        draft_count=len(draft_ids),
        spec_summary="Blueprint request context.",
        decomposition_strategy="Single draft.",
        global_acceptance_intent=("Context visibility is role-scoped.",),
        references=("lab/specs/pending/blueprint.md",),
        created_at=NOW,
    )


def _draft(
    *,
    draft_id: str = "draft-001",
    manifest_id: str = "manifest-001",
    root_spec_id: str = "spec-001",
    latest_blueprint_id: str | None = None,
    latest_critique_id: str | None = None,
) -> BlueprintDraftDocument:
    return BlueprintDraftDocument(
        draft_id=draft_id,
        manifest_id=manifest_id,
        root_spec_id=root_spec_id,
        root_idea_id="idea-001",
        source_spec_id=root_spec_id,
        draft_index=1,
        title="Draft 001",
        summary="Build Blueprint request context.",
        scope=("src/millrace_ai/runtime/request_context.py",),
        target_paths=("src/millrace_ai/runtime/request_context.py",),
        acceptance_intent=("Context boundaries are explicit.",),
        verification_intent=("pytest tests/runtime/test_blueprint_request_context.py -q",),
        context_excerpt="Only request context providers are in scope.",
        current_revision=0,
        latest_blueprint_id=latest_blueprint_id,
        latest_critique_id=latest_critique_id,
        references=("lab/specs/pending/blueprint.md",),
        created_at=NOW,
    )


def _packet(
    blueprint_id: str = "blueprint-001",
    *,
    draft_id: str = "draft-001",
    manifest_id: str = "manifest-001",
    root_spec_id: str = "spec-001",
) -> BlueprintPacketDocument:
    return BlueprintPacketDocument(
        blueprint_id=blueprint_id,
        draft_id=draft_id,
        manifest_id=manifest_id,
        root_spec_id=root_spec_id,
        root_idea_id="idea-001",
        revision=1,
        title="Blueprint request context",
        implementation_scope=("Add Blueprint request context providers.",),
        intended_files=("src/millrace_ai/runtime/request_context.py",),
        design_decisions=("Keep Contractor narrow and Evaluator holistic.",),
        verification_plan=("pytest tests/runtime/test_blueprint_request_context.py -q",),
        task_acceptance=("Context manifests record role boundaries.",),
        required_checks=("pytest tests/runtime/test_blueprint_request_context.py -q",),
        risk_notes=("Context leakage.",),
        references=("lab/specs/pending/blueprint.md",),
        created_at=NOW,
    )


def _critique(
    *,
    critique_id: str = "critique-001",
    evaluation_id: str = "evaluation-001",
    blueprint_id: str = "blueprint-001",
    draft_id: str = "draft-001",
    manifest_id: str = "manifest-001",
    root_spec_id: str = "spec-001",
) -> BlueprintCritiqueDocument:
    return BlueprintCritiqueDocument(
        critique_id=critique_id,
        evaluation_id=evaluation_id,
        blueprint_id=blueprint_id,
        draft_id=draft_id,
        manifest_id=manifest_id,
        root_spec_id=root_spec_id,
        root_idea_id="idea-001",
        revision=1,
        required_changes=("Narrow the visible context.",),
        blocking_reason="Contractor was given too much context.",
        references=("lab/specs/pending/blueprint.md",),
        created_at=NOW,
    )


def _evaluation(
    *,
    evaluation_id: str = "evaluation-001",
    blueprint_id: str = "blueprint-001",
    draft_id: str = "draft-001",
    manifest_id: str = "manifest-001",
    root_spec_id: str = "spec-001",
    critique_id: str = "critique-001",
) -> BlueprintEvaluationDocument:
    return BlueprintEvaluationDocument(
        evaluation_id=evaluation_id,
        blueprint_id=blueprint_id,
        draft_id=draft_id,
        manifest_id=manifest_id,
        root_spec_id=root_spec_id,
        root_idea_id="idea-001",
        decision="rejected",
        rubric_findings=("Context was too broad.",),
        critique_id=critique_id,
        references=("lab/specs/pending/blueprint.md",),
        created_at=NOW,
    )


def _request(
    paths,
    *,
    stage_kind_id: str,
    active_work_item_id: str = "draft-001",
    active_work_item_family_id: str = WorkItemKind.BLUEPRINT_DRAFT.value,
    active_work_item_kind: WorkItemKind = WorkItemKind.BLUEPRINT_DRAFT,
    active_work_item_path: Path | None = None,
    compiled_plan_id: str = "plan-001",
) -> StageRunRequest:
    outcomes_by_stage_kind = {
        "manager_blueprint": {
            "MANAGER_BLUEPRINT_COMPLETE": (ResultClass.SUCCESS,),
            "BLOCKED": (ResultClass.BLOCKED,),
        },
        "contractor_blueprint": {
            "BLUEPRINT_CANDIDATE_READY": (ResultClass.SUCCESS,),
            "BLOCKED": (ResultClass.BLOCKED,),
        },
        "evaluator_blueprint": {
            "BLUEPRINT_APPROVED": (ResultClass.SUCCESS,),
            "BLUEPRINT_REJECTED": (ResultClass.FOLLOWUP_NEEDED,),
            "BLOCKED": (ResultClass.BLOCKED,),
        },
        "mechanic_blueprint": {
            "MECHANIC_BLUEPRINT_COMPLETE": (ResultClass.SUCCESS,),
            "BLOCKED": (ResultClass.BLOCKED,),
        },
    }
    allowed = outcomes_by_stage_kind[stage_kind_id]
    stage = (
        PlanningStageName.MECHANIC
        if stage_kind_id == "mechanic_blueprint"
        else PlanningStageName.MANAGER
    )
    if active_work_item_path is None:
        active_work_item_path = (
            paths.runtime_root / "blueprints/drafts/active" / f"{active_work_item_id}.json"
        )
    return StageRunRequest(
        request_id="req-001",
        run_id=f"run-{stage_kind_id}",
        plane=Plane.PLANNING,
        stage=stage,
        mode_id="blueprint_codex",
        compiled_plan_id=compiled_plan_id,
        node_id=stage_kind_id,
        stage_kind_id=stage_kind_id,
        running_status_marker=f"{stage_kind_id.upper()}_RUNNING",
        legal_terminal_markers=tuple(f"### {outcome}" for outcome in allowed),
        allowed_result_classes_by_outcome=allowed,
        entrypoint_path=f"millrace-agents/entrypoints/planning/{stage_kind_id}.md",
        active_work_item_family_id=active_work_item_family_id,
        active_work_item_kind=active_work_item_kind,
        active_work_item_id=active_work_item_id,
        active_work_item_path=str(active_work_item_path),
        run_dir=str(paths.runs_dir / f"run-{stage_kind_id}"),
        summary_status_path=str(paths.planning_status_file),
        runtime_snapshot_path=str(paths.runtime_snapshot_file),
        recovery_counters_path=str(paths.recovery_counters_file),
    )


def _manifest_for_request(request: StageRunRequest) -> dict[str, object]:
    assert request.rendered_prompt_context_path is not None
    manifest_path = Path(request.rendered_prompt_context_path).with_name("render_manifest.json")
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def _compiled_plan_with_artifact_contracts(
    compiled_plan_id: str = "compiled-plan-with-artifact-contracts",
):
    contracts = {
        contract.artifact_id: contract
        for contract in discover_artifact_contract_definitions()
    }
    return SimpleNamespace(
        compiled_plan_id=compiled_plan_id,
        artifact_contracts_by_id=contracts,
    )


def _write_legacy_manifest_file(paths, manifest: BlueprintManifestDocument) -> Path:
    path = paths.runtime_root / "blueprints" / "manifests" / f"{manifest.root_spec_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def _write_malformed_manifest_file(paths, filename: str = "aaa-malformed.json") -> Path:
    path = paths.runtime_root / "blueprints" / "manifests" / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not valid json", encoding="utf-8")
    return path


def _manager_failure_stage_result(
    *,
    run_id: str,
    work_item_id: str,
    failure_class: str,
    failure_message: str,
    completed_at: datetime = NOW,
    handler_id: str = "manager_blueprint_manifest_to_blueprint_drafts",
) -> StageResultEnvelope:
    return StageResultEnvelope(
        run_id=run_id,
        plane=Plane.PLANNING,
        stage=PlanningStageName.MANAGER,
        node_id="manager_blueprint",
        stage_kind_id="manager_blueprint",
        work_item_kind=WorkItemKind.SPEC,
        work_item_id=work_item_id,
        terminal_result=PlanningTerminalResult.MANAGER_BLUEPRINT_COMPLETE,
        result_class=ResultClass.SUCCESS,
        summary_status_marker="### MANAGER_BLUEPRINT_COMPLETE",
        success=True,
        artifact_paths=("blueprint_manifest.json", "blueprint_drafts.json"),
        metadata={
            "runtime_effect_handler_id": handler_id,
            "runtime_effect_decision": "request_block_source",
            "runtime_effect_failure_class": failure_class,
            "runtime_effect_failure_message": failure_message,
            "runtime_effect_mutation_phase": "pre_mutation",
        },
        started_at=NOW,
        completed_at=completed_at,
    )


def _write_stage_result(path: Path, stage_result: StageResultEnvelope) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(stage_result.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return path


def _manager_recovery_request(
    paths,
    *,
    run_dir: Path,
    work_item_id: str = "spec-001",
) -> StageRunRequest:
    return _request(
        paths,
        stage_kind_id="mechanic_blueprint",
        active_work_item_id=work_item_id,
        active_work_item_family_id=WorkItemKind.SPEC.value,
        active_work_item_kind=WorkItemKind.SPEC,
        active_work_item_path=paths.specs_active_dir / f"{work_item_id}.md",
    ).model_copy(update={"run_id": run_dir.name, "run_dir": str(run_dir)})


def test_contractor_blueprint_context_excludes_full_manifest(tmp_path: Path) -> None:
    paths = _workspace(tmp_path)
    persist_blueprint_critique(paths, _critique())
    enqueue_blueprint_draft(paths, _draft(latest_critique_id="critique-001"))
    assert claim_next_blueprint_draft(paths) is not None

    request = attach_default_request_context(
        workspace_root=paths.root,
        request=_request(paths, stage_kind_id="contractor_blueprint"),
    )
    manifest = _manifest_for_request(request)

    assert request.request_context_profile_id == "contractor_blueprint.default"
    assert "active_blueprint_draft" in manifest["included_provider_ids"]
    assert "draft_context_excerpt" in manifest["included_provider_ids"]
    assert "latest_critique" in manifest["included_provider_ids"]
    assert "blueprint_output_paths" in manifest["included_provider_ids"]
    assert "full_manifest" in manifest["omitted_provider_ids"]
    assert not any("blueprints/manifests" in ref for ref in request.context_artifact_refs)
    assert not any("tasks/queue" in ref for ref in request.context_artifact_refs)


def test_evaluator_blueprint_context_includes_manifest_and_prior_approvals(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    write_blueprint_manifest(paths, _manifest())
    persist_blueprint_packet(paths, _packet("blueprint-000"), packet_state="approved")
    persist_blueprint_packet(paths, _packet("blueprint-001"), packet_state="candidates")
    persist_blueprint_critique(paths, _critique())
    persist_blueprint_evaluation(paths, _evaluation())
    enqueue_blueprint_draft(paths, _draft(latest_blueprint_id="blueprint-001"))
    assert claim_next_blueprint_draft(paths) is not None

    request = attach_default_request_context(
        workspace_root=paths.root,
        request=_request(paths, stage_kind_id="evaluator_blueprint"),
    )
    manifest = _manifest_for_request(request)

    assert request.request_context_profile_id == "evaluator_blueprint.default"
    assert "full_manifest" in manifest["included_provider_ids"]
    assert "candidate_blueprint" in manifest["included_provider_ids"]
    assert "prior_approved_blueprints" in manifest["included_provider_ids"]
    assert "all_blueprint_drafts" in manifest["included_provider_ids"]
    assert "queue_mutation_authority" in manifest["omitted_provider_ids"]
    assert "runtime_control_state" in manifest["redacted_provider_ids"]
    assert any("blueprints/manifests/manifest-001.json" in ref for ref in request.context_artifact_refs)
    assert any("blueprints/packets/approved/blueprint-000.json" in ref for ref in request.context_artifact_refs)
    assert any("blueprints/packets/candidates/blueprint-001.json" in ref for ref in request.context_artifact_refs)
    assert not any("tasks/queue" in ref for ref in request.context_artifact_refs)
    assert any("blueprint_critique.json" in ref for ref in request.context_artifact_refs)
    assert any("generated_task.json" in ref for ref in request.context_artifact_refs)
    assert any("evaluator_blueprint_report.md" in ref for ref in request.context_artifact_refs)
    assert not any("critique_packet.json" in ref for ref in request.context_artifact_refs)
    assert not any("generated_task.md" in ref for ref in request.context_artifact_refs)
    assert not any("blueprint_evaluation_report.md" in ref for ref in request.context_artifact_refs)
    assert manifest["artifact_contract_source"] == "packaged_assets:no_compiled_plan"


def test_evaluator_blueprint_context_excludes_unrelated_root_history(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    write_blueprint_manifest(paths, _manifest())
    persist_blueprint_packet(paths, _packet("blueprint-000"), packet_state="approved")
    persist_blueprint_packet(
        paths,
        _packet(
            "blueprint-unrelated",
            draft_id="draft-unrelated",
            manifest_id="manifest-unrelated",
            root_spec_id="spec-unrelated",
        ),
        packet_state="approved",
    )
    persist_blueprint_critique(paths, _critique())
    persist_blueprint_critique(
        paths,
        _critique(
            critique_id="critique-unrelated",
            evaluation_id="evaluation-unrelated",
            blueprint_id="blueprint-unrelated",
            draft_id="draft-unrelated",
            manifest_id="manifest-unrelated",
            root_spec_id="spec-unrelated",
        ),
    )
    persist_blueprint_evaluation(paths, _evaluation())
    persist_blueprint_evaluation(
        paths,
        _evaluation(
            evaluation_id="evaluation-unrelated",
            blueprint_id="blueprint-unrelated",
            draft_id="draft-unrelated",
            manifest_id="manifest-unrelated",
            root_spec_id="spec-unrelated",
            critique_id="critique-unrelated",
        ),
    )
    enqueue_blueprint_draft(paths, _draft())
    assert claim_next_blueprint_draft(paths) is not None

    request = attach_default_request_context(
        workspace_root=paths.root,
        request=_request(paths, stage_kind_id="evaluator_blueprint"),
    )

    refs = request.context_artifact_refs
    assert any("blueprints/packets/approved/blueprint-000.json" in ref for ref in refs)
    assert any("blueprints/critiques/open/critique-001.json" in ref for ref in refs)
    assert any("blueprints/evaluations/evaluation-001.json" in ref for ref in refs)
    assert not any("blueprint-unrelated" in ref for ref in refs)
    assert not any("critique-unrelated" in ref for ref in refs)
    assert not any("evaluation-unrelated" in ref for ref in refs)


def test_evaluator_blueprint_context_resolves_same_root_manifests_by_manifest_id(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    root_spec_id = "spec-001"
    legacy_manifest = _manifest(
        manifest_id="manifest-original",
        root_spec_id=root_spec_id,
        draft_ids=("draft-original",),
    )
    followup_manifest = _manifest(
        manifest_id="manifest-followup",
        root_spec_id=root_spec_id,
        draft_ids=("draft-followup",),
    )
    legacy_path = _write_legacy_manifest_file(paths, legacy_manifest)
    followup_path = write_blueprint_manifest(paths, followup_manifest)
    _write_malformed_manifest_file(paths)

    enqueue_blueprint_draft(
        paths,
        _draft(
            draft_id="draft-original",
            manifest_id="manifest-original",
            root_spec_id=root_spec_id,
        ),
    )
    assert claim_next_blueprint_draft(paths) is not None
    legacy_request = attach_default_request_context(
        workspace_root=paths.root,
        request=_request(
            paths,
            stage_kind_id="evaluator_blueprint",
            active_work_item_id="draft-original",
        ),
    )
    assert any(str(legacy_path.relative_to(paths.root)) in ref for ref in legacy_request.context_artifact_refs)
    assert not any(str(followup_path.relative_to(paths.root)) in ref for ref in legacy_request.context_artifact_refs)

    approve_active_blueprint_draft(paths, "draft-original")
    enqueue_blueprint_draft(
        paths,
        _draft(
            draft_id="draft-followup",
            manifest_id="manifest-followup",
            root_spec_id=root_spec_id,
        ),
    )
    assert claim_next_blueprint_draft(paths) is not None
    followup_request = attach_default_request_context(
        workspace_root=paths.root,
        request=_request(
            paths,
            stage_kind_id="evaluator_blueprint",
            active_work_item_id="draft-followup",
        ),
    )
    assert any(str(followup_path.relative_to(paths.root)) in ref for ref in followup_request.context_artifact_refs)
    assert not any(str(legacy_path.relative_to(paths.root)) in ref for ref in followup_request.context_artifact_refs)


def test_evaluator_blueprint_context_uses_compiled_artifact_contract_filenames(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    write_blueprint_manifest(paths, _manifest())
    persist_blueprint_packet(paths, _packet("blueprint-001"), packet_state="candidates")
    enqueue_blueprint_draft(paths, _draft(latest_blueprint_id="blueprint-001"))
    assert claim_next_blueprint_draft(paths) is not None

    request = attach_default_request_context(
        workspace_root=paths.root,
        request=_request(
            paths,
            stage_kind_id="evaluator_blueprint",
            compiled_plan_id="compiled-plan-with-artifact-contracts",
        ),
        compiled_plan=_compiled_plan_with_artifact_contracts(
            "compiled-plan-with-artifact-contracts"
        ),
    )
    manifest = _manifest_for_request(request)

    run_dir = Path(request.run_dir)
    expected_refs = {
        f"preferred_output:{(run_dir / 'blueprint_evaluation.json').as_posix()}",
        f"preferred_output:{(run_dir / 'blueprint_critique.json').as_posix()}",
        f"preferred_output:{(run_dir / 'generated_task.json').as_posix()}",
        f"preferred_output:{(run_dir / 'evaluator_blueprint_report.md').as_posix()}",
    }
    stale_filenames = (
        "generated_task.md",
        "critique_packet.json",
        "blueprint_evaluation_report.md",
    )

    assert expected_refs <= set(request.context_artifact_refs)
    assert not any(
        stale_filename in artifact_ref
        for stale_filename in stale_filenames
        for artifact_ref in request.context_artifact_refs
    )
    assert manifest["artifact_contract_source"] == "compiled_plan:compiled-plan-with-artifact-contracts"
    assert manifest["output_artifact_contract_ids"] == [
        "blueprint_evaluation",
        "blueprint_critique",
        "generated_task",
        "blueprint_evaluation_report",
    ]


def test_request_context_rejects_mismatched_compiled_plan_authority(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    write_blueprint_manifest(paths, _manifest())
    persist_blueprint_packet(paths, _packet("blueprint-001"), packet_state="candidates")
    enqueue_blueprint_draft(paths, _draft(latest_blueprint_id="blueprint-001"))
    assert claim_next_blueprint_draft(paths) is not None

    with pytest.raises(ValueError, match="request context compiled plan mismatch"):
        attach_default_request_context(
            workspace_root=paths.root,
            request=_request(
                paths,
                stage_kind_id="evaluator_blueprint",
                compiled_plan_id="launch-plan",
            ),
            compiled_plan=_compiled_plan_with_artifact_contracts("active-engine-plan"),
        )


def test_mechanic_blueprint_context_includes_manager_runtime_effect_failure_evidence(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    run_dir = paths.runs_dir / "run-mechanic-manager-recovery"
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = run_dir / "blueprint_manifest.json"
    drafts_path = run_dir / "blueprint_drafts.json"
    manifest_path.write_text(_manifest().model_dump_json(indent=2) + "\n", encoding="utf-8")
    drafts_path.write_text('{"drafts": []}\n', encoding="utf-8")
    failed_stage_result = StageResultEnvelope(
        run_id="run-mechanic-manager-recovery",
        plane=Plane.PLANNING,
        stage=PlanningStageName.MANAGER,
        node_id="manager_blueprint",
        stage_kind_id="manager_blueprint",
        work_item_kind=WorkItemKind.SPEC,
        work_item_id="spec-001",
        terminal_result=PlanningTerminalResult.MANAGER_BLUEPRINT_COMPLETE,
        result_class=ResultClass.SUCCESS,
        summary_status_marker="### MANAGER_BLUEPRINT_COMPLETE",
        success=True,
        artifact_paths=("blueprint_manifest.json", "blueprint_drafts.json"),
        metadata={
            "runtime_effect_handler_id": "manager_blueprint_manifest_to_blueprint_drafts",
            "runtime_effect_decision": "request_block_source",
            "runtime_effect_failure_class": "blueprint_manifest_duplicate",
            "runtime_effect_failure_message": (
                "Blueprint artifact already exists: "
                "millrace-agents/blueprints/manifests/manifest-001.json"
            ),
            "runtime_effect_mutation_phase": "pre_mutation",
        },
        started_at=NOW,
        completed_at=NOW,
    )
    stage_result_path = run_dir / "stage_results" / "request-manager.json"
    stage_result_path.parent.mkdir(parents=True, exist_ok=True)
    stage_result_path.write_text(
        failed_stage_result.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )

    request = _request(
        paths,
        stage_kind_id="mechanic_blueprint",
        active_work_item_id="spec-001",
        active_work_item_family_id=WorkItemKind.SPEC.value,
        active_work_item_kind=WorkItemKind.SPEC,
        active_work_item_path=paths.specs_active_dir / "spec-001.md",
    ).model_copy(update={"run_id": "run-mechanic-manager-recovery", "run_dir": str(run_dir)})
    request = attach_default_request_context(workspace_root=paths.root, request=request)
    manifest = _manifest_for_request(request)
    refs = set(request.context_artifact_refs)

    assert "manager_runtime_effect_failure_context" in manifest["included_provider_ids"]
    assert f"failed_manager_run_dir:{run_dir.relative_to(paths.root).as_posix()}" in refs
    assert f"failed_stage_result:{stage_result_path.relative_to(paths.root).as_posix()}" in refs
    assert "runtime_effect_failure_class:blueprint_manifest_duplicate" in refs
    assert (
        "runtime_effect_failure_message:Blueprint artifact already exists: "
        "millrace-agents/blueprints/manifests/manifest-001.json"
    ) in refs
    assert f"failed_manager_artifact:{manifest_path.relative_to(paths.root).as_posix()}" in refs
    assert f"failed_manager_artifact:{drafts_path.relative_to(paths.root).as_posix()}" in refs
    assert f"preferred_output:{(run_dir / 'blueprint_repair_decision.json').as_posix()}" in refs
    assert f"preferred_output:{(run_dir / 'repaired_generated_task.json').as_posix()}" in refs
    assert f"preferred_output:{(run_dir / 'mechanic_report.md').as_posix()}" in refs
    assert not any("repaired_blueprint_artifact" in ref for ref in refs)
    assert "queue_mutation_authority" in manifest["omitted_provider_ids"]
    assert manifest["output_artifact_contract_ids"] == [
        "blueprint_repair_decision",
        "repaired_generated_task",
        "mechanic_report",
    ]


def test_mechanic_blueprint_context_includes_evaluator_runtime_effect_failure_evidence(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    run_dir = paths.runs_dir / "run-mechanic-evaluator-recovery"
    run_dir.mkdir(parents=True, exist_ok=True)
    evaluation_path = run_dir / "blueprint_evaluation.json"
    generated_task_path = run_dir / "generated_task.md"
    evaluation_path.write_text('{"evaluation_id":"evaluation-001"}\n', encoding="utf-8")
    generated_task_path.write_text(
        "# Invalid generated task\n\nTask-ID: task-draft-001\n",
        encoding="utf-8",
    )
    failed_stage_result = StageResultEnvelope(
        run_id="run-mechanic-evaluator-recovery",
        plane=Plane.PLANNING,
        stage=PlanningStageName.MANAGER,
        node_id="evaluator_blueprint",
        stage_kind_id="evaluator_blueprint",
        work_item_kind=WorkItemKind.BLUEPRINT_DRAFT,
        work_item_id="draft-001",
        terminal_result=PlanningTerminalResult.BLUEPRINT_APPROVED,
        result_class=ResultClass.SUCCESS,
        summary_status_marker="### BLUEPRINT_APPROVED",
        success=True,
        artifact_paths=("blueprint_evaluation.json", "generated_task.md"),
        metadata={
            "runtime_effect_handler_id": "evaluator_blueprint_approved_to_task",
            "runtime_effect_decision": "request_block_source",
            "runtime_effect_failure_class": "generated_task_invalid",
            "runtime_effect_failure_message": "generated_task.md failed schema validation",
            "runtime_effect_mutation_phase": "pre_mutation",
            "runtime_effect_failure_policy_id": (
                "blueprint_approval_pre_mutation_effect_validation"
            ),
            "runtime_effect_recovery_action": "route_to_node",
        },
        started_at=NOW,
        completed_at=NOW,
    )
    stage_result_path = run_dir / "stage_results" / "request-evaluator.json"
    stage_result_path.parent.mkdir(parents=True, exist_ok=True)
    stage_result_path.write_text(
        failed_stage_result.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )

    request = _request(
        paths,
        stage_kind_id="mechanic_blueprint",
        active_work_item_id="draft-001",
        active_work_item_family_id=WorkItemKind.BLUEPRINT_DRAFT.value,
        active_work_item_kind=WorkItemKind.BLUEPRINT_DRAFT,
        active_work_item_path=paths.runtime_root / "blueprints/drafts/active/draft-001.json",
    ).model_copy(update={"run_id": "run-mechanic-evaluator-recovery", "run_dir": str(run_dir)})
    request = attach_default_request_context(workspace_root=paths.root, request=request)
    manifest = _manifest_for_request(request)
    refs = set(request.context_artifact_refs)

    assert "evaluator_runtime_effect_failure_context" in manifest["included_provider_ids"]
    assert f"failed_evaluator_run_dir:{run_dir.relative_to(paths.root).as_posix()}" in refs
    assert f"failed_stage_result:{stage_result_path.relative_to(paths.root).as_posix()}" in refs
    assert "runtime_effect_handler_id:evaluator_blueprint_approved_to_task" in refs
    assert "runtime_effect_failure_class:generated_task_invalid" in refs
    assert "runtime_effect_mutation_phase:pre_mutation" in refs
    assert (
        "runtime_effect_failure_policy_id:"
        "blueprint_approval_pre_mutation_effect_validation"
    ) in refs
    assert "runtime_effect_recovery_action:route_to_node" in refs
    assert f"failed_evaluator_artifact:{evaluation_path.relative_to(paths.root).as_posix()}" in refs
    assert f"failed_evaluator_artifact:{generated_task_path.relative_to(paths.root).as_posix()}" in refs
    assert "required_repair_action:apply_repaired_generated_task" in refs
    assert "runtime_owns_blueprint_state:true" in refs
    assert f"preferred_output:{(run_dir / 'blueprint_repair_decision.json').as_posix()}" in refs
    assert f"preferred_output:{(run_dir / 'repaired_generated_task.json').as_posix()}" in refs
    assert f"preferred_output:{(run_dir / 'mechanic_report.md').as_posix()}" in refs
    assert not any("repaired_blueprint_artifact" in ref for ref in refs)
    assert "queue_mutation_authority" in manifest["omitted_provider_ids"]


def test_mechanic_blueprint_context_prefers_manager_failure_matching_request(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    run_dir = paths.runs_dir / "run-mechanic-manager-recovery"
    run_dir.mkdir(parents=True, exist_ok=True)
    current_path = _write_stage_result(
        run_dir / "stage_results" / "aaa-current.json",
        _manager_failure_stage_result(
            run_id=run_dir.name,
            work_item_id="spec-001",
            failure_class="current_manager_failure",
            failure_message="current failure",
            completed_at=NOW,
        ),
    )
    _write_stage_result(
        run_dir / "stage_results" / "yyy-wrong-handler.json",
        _manager_failure_stage_result(
            run_id=run_dir.name,
            work_item_id="spec-001",
            failure_class="wrong_handler_failure",
            failure_message="wrong handler failure",
            completed_at=NOW + timedelta(minutes=20),
            handler_id="evaluator_blueprint_approved_to_task",
        ),
    )
    _write_stage_result(
        run_dir / "stage_results" / "zzz-stale.json",
        _manager_failure_stage_result(
            run_id=run_dir.name,
            work_item_id="spec-stale",
            failure_class="stale_manager_failure",
            failure_message="stale failure",
            completed_at=NOW + timedelta(minutes=10),
        ),
    )

    request = attach_default_request_context(
        workspace_root=paths.root,
        request=_manager_recovery_request(paths, run_dir=run_dir, work_item_id="spec-001"),
    )
    refs = set(request.context_artifact_refs)

    assert f"failed_stage_result:{current_path.relative_to(paths.root).as_posix()}" in refs
    assert "runtime_effect_failure_class:current_manager_failure" in refs
    assert not any("stale_manager_failure" in ref for ref in refs)
    assert not any("wrong_handler_failure" in ref for ref in refs)


def test_mechanic_blueprint_context_falls_back_to_latest_completed_manager_failure(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    run_dir = paths.runs_dir / "run-mechanic-manager-recovery"
    run_dir.mkdir(parents=True, exist_ok=True)
    latest_path = _write_stage_result(
        run_dir / "stage_results" / "aaa-latest.json",
        _manager_failure_stage_result(
            run_id=run_dir.name,
            work_item_id="spec-latest",
            failure_class="latest_manager_failure",
            failure_message="latest failure",
            completed_at=NOW + timedelta(minutes=5),
        ),
    )
    _write_stage_result(
        run_dir / "stage_results" / "zzz-older.json",
        _manager_failure_stage_result(
            run_id=run_dir.name,
            work_item_id="spec-older",
            failure_class="older_manager_failure",
            failure_message="older failure",
            completed_at=NOW,
        ),
    )

    request = attach_default_request_context(
        workspace_root=paths.root,
        request=_manager_recovery_request(paths, run_dir=run_dir, work_item_id="spec-001"),
    )
    refs = set(request.context_artifact_refs)

    assert f"failed_stage_result:{latest_path.relative_to(paths.root).as_posix()}" in refs
    assert "runtime_effect_failure_class:latest_manager_failure" in refs
    assert not any("older_manager_failure" in ref for ref in refs)


def test_mechanic_blueprint_context_normalizes_multiline_failure_message_refs(
    tmp_path: Path,
) -> None:
    paths = _workspace(tmp_path)
    run_dir = paths.runs_dir / "run-mechanic-manager-recovery"
    run_dir.mkdir(parents=True, exist_ok=True)
    _write_stage_result(
        run_dir / "stage_results" / "request-manager.json",
        _manager_failure_stage_result(
            run_id=run_dir.name,
            work_item_id="spec-001",
            failure_class="blueprint_manifest_duplicate",
            failure_message="Blueprint artifact already exists:\n\n- manifests/manifest-001.json",
        ),
    )

    request = attach_default_request_context(
        workspace_root=paths.root,
        request=_manager_recovery_request(paths, run_dir=run_dir, work_item_id="spec-001"),
    )
    message_refs = [
        ref
        for ref in request.context_artifact_refs
        if ref.startswith("runtime_effect_failure_message:")
    ]

    assert message_refs == [
        "runtime_effect_failure_message:Blueprint artifact already exists: "
        "- manifests/manifest-001.json"
    ]
    assert all("\n" not in ref for ref in message_refs)
