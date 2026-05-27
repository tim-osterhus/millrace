"""Built-in Blueprint request-context provider implementations."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Mapping

from millrace_ai.architecture import ArtifactContractDefinition, CompiledRunPlan
from millrace_ai.assets import discover_artifact_contract_definitions
from millrace_ai.contracts import (
    BlueprintCritiqueDocument,
    BlueprintDraftDocument,
    BlueprintEvaluationDocument,
    BlueprintPacketDocument,
    StageResultEnvelope,
    WorkItemKind,
)
from millrace_ai.runners import StageRunRequest
from millrace_ai.workspace.blueprint_state import (
    read_active_blueprint_draft,
    read_blueprint_draft,
    resolve_blueprint_manifest_path,
)
from millrace_ai.workspace.paths import WorkspacePaths, workspace_paths

from .generic import visible_artifact_refs
from .models import RequestContextAuthority, RequestContextRenderPlan


@dataclass(frozen=True, slots=True)
class _ArtifactContractSet:
    source: str
    contracts_by_id: Mapping[str, ArtifactContractDefinition]


@dataclass(frozen=True, slots=True)
class _ManagerRuntimeEffectFailure:
    stage_result_path: Path
    stage_result: StageResultEnvelope


@dataclass(frozen=True, slots=True)
class _EvaluatorRuntimeEffectFailure:
    stage_result_path: Path
    stage_result: StageResultEnvelope


_MANAGER_BLUEPRINT_RUNTIME_EFFECT_HANDLER_ID = "manager_blueprint_manifest_to_blueprint_drafts"
_EVALUATOR_BLUEPRINT_RUNTIME_EFFECT_HANDLER_ID = "evaluator_blueprint_approved_to_task"


def blueprint_manager_context_plan(
    workspace_root: Path,
    request: StageRunRequest,
    authority: RequestContextAuthority,
    compiled_plan: CompiledRunPlan | None,
) -> RequestContextRenderPlan:
    paths = workspace_paths(workspace_root)
    artifact_contracts = _artifact_contracts_for_request(
        compiled_plan,
        request_compiled_plan_id=request.compiled_plan_id,
    )
    output_artifact_ids = ("blueprint_manifest", "blueprint_drafts")
    visible_refs = [
        *visible_artifact_refs(request),
        *_preferred_blueprint_contract_output_refs(
            request,
            output_artifact_ids,
            artifact_contracts=artifact_contracts,
        ),
        _artifact_ref(paths, Path(request.run_dir) / "manager_blueprint_report.md"),
    ]
    return _blueprint_plan(
        request,
        authority=authority,
        visible_artifact_refs=_unique_tuple(tuple(visible_refs)),
        included_provider_ids=(
            "active_source_work_item",
            "root_lineage",
            "blueprint_output_paths",
        ),
        inline_sections=("active_work_item", "root_lineage", "blueprint_output_paths"),
        omitted_provider_ids=(
            "execution_queue_mutation_authority",
            "direct_blueprint_store_write_authority",
        ),
        artifact_contract_source=artifact_contracts.source,
        output_artifact_contract_ids=output_artifact_ids,
    )


def blueprint_contractor_context_plan(
    workspace_root: Path,
    request: StageRunRequest,
    authority: RequestContextAuthority,
    compiled_plan: CompiledRunPlan | None,
) -> RequestContextRenderPlan:
    paths = workspace_paths(workspace_root)
    artifact_contracts = _artifact_contracts_for_request(
        compiled_plan,
        request_compiled_plan_id=request.compiled_plan_id,
    )
    output_artifact_ids = ("blueprint_packet", "blueprint_markdown")
    draft = _active_blueprint_draft_for_request(paths, request)
    visible_refs = [
        _active_blueprint_draft_ref(paths, draft.draft_id),
        *_critique_refs(paths, critique_id=draft.latest_critique_id),
        *_latest_rejected_blueprint_refs(paths, blueprint_id=draft.latest_blueprint_id),
        *_preferred_blueprint_contract_output_refs(
            request,
            output_artifact_ids,
            artifact_contracts=artifact_contracts,
        ),
        _artifact_ref(paths, Path(request.run_dir) / "contractor_blueprint_report.md"),
    ]
    included_provider_ids = [
        "active_blueprint_draft",
        "draft_context_excerpt",
        "blueprint_output_paths",
    ]
    if draft.latest_critique_id is not None:
        included_provider_ids.append("latest_critique")
    if draft.latest_blueprint_id is not None:
        included_provider_ids.append("latest_rejected_blueprint")
    return _blueprint_plan(
        request,
        authority=authority,
        visible_artifact_refs=_unique_tuple(tuple(visible_refs)),
        included_provider_ids=tuple(included_provider_ids),
        inline_sections=("draft_context_excerpt", "blueprint_output_paths"),
        omitted_provider_ids=(
            "full_manifest",
            "all_blueprint_drafts",
            "prior_approved_blueprints",
            "queue_mutation_authority",
        ),
        artifact_contract_source=artifact_contracts.source,
        output_artifact_contract_ids=output_artifact_ids,
    )


def blueprint_evaluator_context_plan(
    workspace_root: Path,
    request: StageRunRequest,
    authority: RequestContextAuthority,
    compiled_plan: CompiledRunPlan | None,
) -> RequestContextRenderPlan:
    paths = workspace_paths(workspace_root)
    artifact_contracts = _artifact_contracts_for_request(
        compiled_plan,
        request_compiled_plan_id=request.compiled_plan_id,
    )
    output_artifact_ids = (
        "blueprint_evaluation",
        "blueprint_critique",
        "generated_task",
        "blueprint_evaluation_report",
    )
    draft = _active_blueprint_draft_for_request(paths, request)
    manifest_path = resolve_blueprint_manifest_path(paths, draft.manifest_id)
    visible_refs = [
        _active_blueprint_draft_ref(paths, draft.draft_id),
        _artifact_ref(paths, manifest_path),
        *_candidate_blueprint_refs(paths, blueprint_id=draft.latest_blueprint_id),
        *_blueprint_draft_refs_for_root(paths, root_spec_id=draft.root_spec_id),
        *_approved_blueprint_refs(paths, root_spec_id=draft.root_spec_id),
        *_critique_refs(paths, root_spec_id=draft.root_spec_id),
        *_evaluation_refs(paths, root_spec_id=draft.root_spec_id),
        *_original_spec_refs(paths, spec_id=draft.source_spec_id),
        *_preferred_blueprint_contract_output_refs(
            request,
            output_artifact_ids,
            artifact_contracts=artifact_contracts,
        ),
    ]
    return _blueprint_plan(
        request,
        authority=authority,
        visible_artifact_refs=_existing_or_preferred_refs(paths, tuple(visible_refs)),
        included_provider_ids=(
            "active_blueprint_draft",
            "full_manifest",
            "candidate_blueprint",
            "prior_approved_blueprints",
            "all_blueprint_drafts",
            "critique_history",
            "evaluation_history",
            "original_spec",
            "blueprint_output_paths",
        ),
        inline_sections=("evaluation_scope", "blueprint_output_paths"),
        omitted_provider_ids=("queue_mutation_authority",),
        artifact_contract_source=artifact_contracts.source,
        output_artifact_contract_ids=output_artifact_ids,
    )


def blueprint_mechanic_context_plan(
    workspace_root: Path,
    request: StageRunRequest,
    authority: RequestContextAuthority,
    compiled_plan: CompiledRunPlan | None,
) -> RequestContextRenderPlan:
    paths = workspace_paths(workspace_root)
    artifact_contracts = _artifact_contracts_for_request(
        compiled_plan,
        request_compiled_plan_id=request.compiled_plan_id,
    )
    output_artifact_ids = (
        "blueprint_repair_decision",
        "repaired_generated_task",
        "mechanic_report",
    )
    manager_failure_refs = _manager_runtime_effect_failure_refs_from_recovery_run_dir(
        paths,
        request,
    )
    evaluator_failure_refs = _evaluator_runtime_effect_failure_refs_from_recovery_run_dir(
        paths,
        request,
    )
    included_provider_ids = [
        "active_work_item",
        "runtime_failure_context",
        "blueprint_repair_output_paths",
    ]
    inline_sections = [
        "runtime_failure_context",
        "blueprint_repair_output_paths",
    ]
    if manager_failure_refs:
        included_provider_ids.append("manager_runtime_effect_failure_context")
        inline_sections.append("manager_runtime_effect_failure_context")
    if evaluator_failure_refs:
        included_provider_ids.append("evaluator_runtime_effect_failure_context")
        inline_sections.append("evaluator_runtime_effect_failure_context")
    visible_refs = [
        *visible_artifact_refs(request),
        *manager_failure_refs,
        *evaluator_failure_refs,
        *_preferred_blueprint_contract_output_refs(
            request,
            output_artifact_ids,
            artifact_contracts=artifact_contracts,
        ),
    ]
    return _blueprint_plan(
        request,
        authority=authority,
        visible_artifact_refs=_unique_tuple(tuple(visible_refs)),
        included_provider_ids=tuple(included_provider_ids),
        inline_sections=tuple(inline_sections),
        omitted_provider_ids=("queue_mutation_authority",),
        artifact_contract_source=artifact_contracts.source,
        output_artifact_contract_ids=output_artifact_ids,
    )


def built_in_blueprint_provider_registrations() -> tuple[tuple[str, object], ...]:
    return (
        ("blueprint.manager", blueprint_manager_context_plan),
        ("blueprint.contractor", blueprint_contractor_context_plan),
        ("blueprint.evaluator", blueprint_evaluator_context_plan),
        ("blueprint.mechanic", blueprint_mechanic_context_plan),
    )


def _blueprint_plan(
    request: StageRunRequest,
    *,
    authority: RequestContextAuthority,
    visible_artifact_refs: tuple[str, ...],
    included_provider_ids: tuple[str, ...],
    inline_sections: tuple[str, ...],
    omitted_provider_ids: tuple[str, ...],
    artifact_contract_source: str | None = None,
    output_artifact_contract_ids: tuple[str, ...] = (),
) -> RequestContextRenderPlan:
    run_dir = Path(request.run_dir)
    context_dir = run_dir / "context"
    return RequestContextRenderPlan(
        render_plan_id=authority.render_plan_id,
        profile_id=authority.profile_id,
        context_bundle_path=str(context_dir / "context.json"),
        rendered_prompt_context_path=str(context_dir / "prompt_context.md"),
        visible_artifact_refs=visible_artifact_refs,
        operator_only_artifact_refs=(
            f"runtime_snapshot:{request.runtime_snapshot_path}",
            f"recovery_counters:{request.recovery_counters_path}",
        ),
        included_provider_ids=included_provider_ids,
        redacted_provider_ids=("runtime_control_state",),
        inline_sections=inline_sections,
        omitted_provider_ids=omitted_provider_ids,
        artifact_contract_source=artifact_contract_source,
        output_artifact_contract_ids=output_artifact_contract_ids,
    )


def _active_blueprint_draft_for_request(
    paths: WorkspacePaths,
    request: StageRunRequest,
) -> BlueprintDraftDocument:
    if request.active_work_item_family_id != WorkItemKind.BLUEPRINT_DRAFT.value:
        raise ValueError("Blueprint context requires an active blueprint_draft")
    if request.active_work_item_id is None:
        raise ValueError("Blueprint context requires an active draft id")
    return read_active_blueprint_draft(paths, request.active_work_item_id)


def _active_blueprint_draft_ref(paths: WorkspacePaths, draft_id: str) -> str:
    return _artifact_ref(
        paths,
        paths.runtime_root / "blueprints" / "drafts" / "active" / f"{draft_id}.json",
    )


def _preferred_blueprint_contract_output_refs(
    request: StageRunRequest,
    artifact_ids: tuple[str, ...],
    *,
    artifact_contracts: _ArtifactContractSet,
) -> tuple[str, ...]:
    run_dir = Path(request.run_dir)
    return tuple(
        f"preferred_output:{(run_dir / _canonical_artifact_filename(artifact_contracts, artifact_id)).as_posix()}"
        for artifact_id in artifact_ids
    )


def _canonical_artifact_filename(
    artifact_contracts: _ArtifactContractSet,
    artifact_id: str,
) -> str:
    contract = artifact_contracts.contracts_by_id.get(artifact_id)
    if contract is None:
        raise ValueError(
            f"Artifact contract {artifact_id!r} is unavailable from {artifact_contracts.source}"
        )
    return contract.canonical_filename


def _manager_runtime_effect_failure_refs_from_recovery_run_dir(
    paths: WorkspacePaths,
    request: StageRunRequest,
) -> tuple[str, ...]:
    run_dir = Path(request.run_dir)
    failure = _manager_runtime_effect_failure_from_recovery_run_dir(
        request=request,
        run_dir=run_dir,
    )
    if failure is None:
        return ()

    refs = [
        f"failed_manager_run_dir:{_artifact_ref(paths, run_dir)}",
        f"failed_stage_result:{_artifact_ref(paths, failure.stage_result_path)}",
    ]
    failure_class = _string_metadata(failure.stage_result, "runtime_effect_failure_class")
    failure_message = _string_metadata(failure.stage_result, "runtime_effect_failure_message")
    if failure_class is not None:
        refs.append(f"runtime_effect_failure_class:{failure_class}")
    if failure_message is not None:
        refs.append(f"runtime_effect_failure_message:{failure_message}")
    refs.extend(
        f"failed_manager_artifact:{_artifact_ref(paths, run_dir / filename)}"
        for filename in ("blueprint_manifest.json", "blueprint_drafts.json")
    )
    return _unique_tuple(tuple(refs))


def _evaluator_runtime_effect_failure_refs_from_recovery_run_dir(
    paths: WorkspacePaths,
    request: StageRunRequest,
) -> tuple[str, ...]:
    run_dir = Path(request.run_dir)
    failure = _evaluator_runtime_effect_failure_from_recovery_run_dir(
        request=request,
        run_dir=run_dir,
    )
    if failure is None:
        return ()

    refs = [
        f"failed_evaluator_run_dir:{_artifact_ref(paths, run_dir)}",
        f"failed_stage_result:{_artifact_ref(paths, failure.stage_result_path)}",
    ]
    for key in (
        "runtime_effect_handler_id",
        "runtime_effect_failure_class",
        "runtime_effect_failure_message",
        "runtime_effect_mutation_phase",
        "runtime_effect_failure_policy_id",
        "runtime_effect_recovery_action",
    ):
        value = _string_metadata(failure.stage_result, key)
        if value is not None:
            refs.append(f"{key}:{value}")
    refs.extend(
        f"failed_evaluator_artifact:{_artifact_ref(paths, run_dir / filename)}"
        for filename in ("blueprint_evaluation.json", "generated_task.md")
    )
    refs.extend(
        (
            "required_repair_action:apply_repaired_generated_task",
            "runtime_owns_blueprint_state:true",
        )
    )
    return _unique_tuple(tuple(refs))


def _manager_runtime_effect_failure_from_recovery_run_dir(
    *,
    request: StageRunRequest,
    run_dir: Path,
) -> _ManagerRuntimeEffectFailure | None:
    """Select Manager failure evidence from the recovery run directory.

    Runtime-effect recovery reuses the failed Manager run directory for
    Mechanic Blueprint so the request can inspect pre-mutation Manager outputs.
    """

    stage_results_dir = run_dir / "stage_results"
    candidates: list[_ManagerRuntimeEffectFailure] = []
    for path in _json_files(stage_results_dir):
        try:
            stage_result = StageResultEnvelope.model_validate_json(
                path.read_text(encoding="utf-8")
            )
        except ValueError:
            continue
        if not _is_manager_runtime_effect_failure(stage_result):
            continue
        candidates.append(
            _ManagerRuntimeEffectFailure(
                stage_result_path=path,
                stage_result=stage_result,
            )
        )
    if not candidates:
        return None
    exact_matches = [
        failure
        for failure in candidates
        if _stage_result_matches_recovery_request(failure.stage_result, request)
    ]
    if exact_matches:
        return _latest_completed_manager_failure(exact_matches)
    return _latest_completed_manager_failure(candidates)


def _evaluator_runtime_effect_failure_from_recovery_run_dir(
    *,
    request: StageRunRequest,
    run_dir: Path,
) -> _EvaluatorRuntimeEffectFailure | None:
    stage_results_dir = run_dir / "stage_results"
    candidates: list[_EvaluatorRuntimeEffectFailure] = []
    for path in _json_files(stage_results_dir):
        try:
            stage_result = StageResultEnvelope.model_validate_json(
                path.read_text(encoding="utf-8")
            )
        except ValueError:
            continue
        if not _is_evaluator_runtime_effect_failure(stage_result):
            continue
        candidates.append(
            _EvaluatorRuntimeEffectFailure(
                stage_result_path=path,
                stage_result=stage_result,
            )
        )
    if not candidates:
        return None
    exact_matches = [
        failure
        for failure in candidates
        if _stage_result_matches_recovery_request(failure.stage_result, request)
    ]
    if exact_matches:
        return _latest_completed_evaluator_failure(exact_matches)
    return _latest_completed_evaluator_failure(candidates)


def _is_manager_runtime_effect_failure(stage_result: StageResultEnvelope) -> bool:
    if stage_result.stage_kind_id != "manager_blueprint":
        return False
    if stage_result.terminal_result.value != "MANAGER_BLUEPRINT_COMPLETE":
        return False
    if (
        stage_result.metadata.get("runtime_effect_handler_id")
        != _MANAGER_BLUEPRINT_RUNTIME_EFFECT_HANDLER_ID
    ):
        return False
    if stage_result.metadata.get("runtime_effect_decision") != "request_block_source":
        return False
    return _string_metadata(stage_result, "runtime_effect_failure_class") is not None


def _is_evaluator_runtime_effect_failure(stage_result: StageResultEnvelope) -> bool:
    if stage_result.stage_kind_id != "evaluator_blueprint":
        return False
    if stage_result.terminal_result.value != "BLUEPRINT_APPROVED":
        return False
    if (
        stage_result.metadata.get("runtime_effect_handler_id")
        != _EVALUATOR_BLUEPRINT_RUNTIME_EFFECT_HANDLER_ID
    ):
        return False
    if stage_result.metadata.get("runtime_effect_decision") != "request_block_source":
        return False
    failure_class = _string_metadata(stage_result, "runtime_effect_failure_class")
    return failure_class in {"generated_task_invalid", "generated_task_missing"}


def _stage_result_matches_recovery_request(
    stage_result: StageResultEnvelope,
    request: StageRunRequest,
) -> bool:
    if request.run_id and stage_result.run_id != request.run_id:
        return False
    if request.active_work_item_id and stage_result.work_item_id != request.active_work_item_id:
        return False
    return True


def _latest_completed_manager_failure(
    failures: list[_ManagerRuntimeEffectFailure],
) -> _ManagerRuntimeEffectFailure:
    return max(
        failures,
        key=lambda failure: (
            failure.stage_result.completed_at,
            failure.stage_result.run_id,
            failure.stage_result.work_item_id,
            failure.stage_result_path.as_posix(),
        ),
    )


def _latest_completed_evaluator_failure(
    failures: list[_EvaluatorRuntimeEffectFailure],
) -> _EvaluatorRuntimeEffectFailure:
    return max(
        failures,
        key=lambda failure: (
            failure.stage_result.completed_at,
            failure.stage_result.run_id,
            failure.stage_result.work_item_id,
            failure.stage_result_path.as_posix(),
        ),
    )


def _string_metadata(stage_result: StageResultEnvelope, key: str) -> str | None:
    value = stage_result.metadata.get(key)
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.strip().split())
    return normalized or None


def _artifact_contracts_for_request(
    compiled_plan: CompiledRunPlan | None,
    *,
    request_compiled_plan_id: str,
) -> _ArtifactContractSet:
    contracts_by_id = getattr(compiled_plan, "artifact_contracts_by_id", {}) if compiled_plan else {}
    if compiled_plan is not None and contracts_by_id:
        if compiled_plan.compiled_plan_id != request_compiled_plan_id:
            raise ValueError(
                "request context compiled plan mismatch: "
                f"request references {request_compiled_plan_id}, "
                f"but artifact contracts came from {compiled_plan.compiled_plan_id}"
            )
        return _ArtifactContractSet(
            source=f"compiled_plan:{compiled_plan.compiled_plan_id}",
            contracts_by_id=contracts_by_id,
        )
    return _ArtifactContractSet(
        source="packaged_assets:no_compiled_plan",
        contracts_by_id=_packaged_artifact_contracts_by_id(),
    )


@lru_cache(maxsize=1)
def _packaged_artifact_contracts_by_id() -> dict[str, ArtifactContractDefinition]:
    return {
        contract.artifact_id: contract
        for contract in discover_artifact_contract_definitions()
    }


def _critique_refs(
    paths: WorkspacePaths,
    *,
    critique_id: str | None = None,
    root_spec_id: str | None = None,
) -> tuple[str, ...]:
    critique_dirs = (
        paths.runtime_root / "blueprints" / "critiques" / "open",
        paths.runtime_root / "blueprints" / "critiques" / "resolved",
    )
    refs: list[str] = []
    for directory in critique_dirs:
        if critique_id is not None:
            path = directory / f"{critique_id}.json"
            if path.exists():
                refs.append(_artifact_ref(paths, path))
            continue
        for path in _json_files(directory):
            if root_spec_id is not None and not _critique_matches_root(path, root_spec_id):
                continue
            refs.append(_artifact_ref(paths, path))
    return tuple(refs)


def _latest_rejected_blueprint_refs(
    paths: WorkspacePaths,
    *,
    blueprint_id: str | None,
) -> tuple[str, ...]:
    if blueprint_id is None:
        return ()
    rejected_path = paths.runtime_root / "blueprints" / "packets" / "rejected" / f"{blueprint_id}.json"
    if not rejected_path.exists():
        return ()
    return (_artifact_ref(paths, rejected_path),)


def _candidate_blueprint_refs(
    paths: WorkspacePaths,
    *,
    blueprint_id: str | None,
) -> tuple[str, ...]:
    if blueprint_id is None:
        return ()
    candidate_path = paths.runtime_root / "blueprints" / "packets" / "candidates" / f"{blueprint_id}.json"
    if not candidate_path.exists():
        return ()
    return (_artifact_ref(paths, candidate_path),)


def _approved_blueprint_refs(
    paths: WorkspacePaths,
    *,
    root_spec_id: str | None = None,
) -> tuple[str, ...]:
    refs: list[str] = []
    for path in _json_files(paths.runtime_root / "blueprints" / "packets" / "approved"):
        if root_spec_id is not None and not _packet_matches_root(path, root_spec_id):
            continue
        refs.append(_artifact_ref(paths, path))
    return tuple(refs)


def _evaluation_refs(
    paths: WorkspacePaths,
    *,
    root_spec_id: str | None = None,
) -> tuple[str, ...]:
    refs: list[str] = []
    for path in _json_files(paths.runtime_root / "blueprints" / "evaluations"):
        if root_spec_id is not None and not _evaluation_matches_root(path, root_spec_id):
            continue
        refs.append(_artifact_ref(paths, path))
    return tuple(refs)


def _packet_matches_root(path: Path, root_spec_id: str) -> bool:
    try:
        packet = BlueprintPacketDocument.model_validate_json(path.read_text(encoding="utf-8"))
    except ValueError:
        return False
    return packet.root_spec_id == root_spec_id


def _critique_matches_root(path: Path, root_spec_id: str) -> bool:
    try:
        critique = BlueprintCritiqueDocument.model_validate_json(path.read_text(encoding="utf-8"))
    except ValueError:
        return False
    return critique.root_spec_id == root_spec_id


def _evaluation_matches_root(path: Path, root_spec_id: str) -> bool:
    try:
        evaluation = BlueprintEvaluationDocument.model_validate_json(path.read_text(encoding="utf-8"))
    except ValueError:
        return False
    return evaluation.root_spec_id == root_spec_id


def _blueprint_draft_refs_for_root(
    paths: WorkspacePaths,
    *,
    root_spec_id: str,
) -> tuple[str, ...]:
    refs: list[str] = []
    drafts_root = paths.runtime_root / "blueprints" / "drafts"
    for state in ("queue", "active", "approved", "blocked", "canceled", "superseded"):
        for path in _json_files(drafts_root / state):
            try:
                draft = read_blueprint_draft(path)
            except ValueError:
                refs.append(_artifact_ref(paths, path))
                continue
            if draft.root_spec_id == root_spec_id:
                refs.append(_artifact_ref(paths, path))
    return tuple(refs)


def _original_spec_refs(
    paths: WorkspacePaths,
    *,
    spec_id: str,
) -> tuple[str, ...]:
    for directory in (
        paths.specs_active_dir,
        paths.specs_done_dir,
        paths.specs_queue_dir,
        paths.specs_blocked_dir,
    ):
        path = directory / f"{spec_id}.md"
        if path.exists():
            return (_artifact_ref(paths, path),)
    return ()


def _existing_or_preferred_refs(paths: WorkspacePaths, refs: tuple[str, ...]) -> tuple[str, ...]:
    kept: list[str] = []
    for ref in refs:
        if ref.startswith("preferred_output:"):
            kept.append(ref)
            continue
        if (paths.root / ref).exists():
            kept.append(ref)
    return _unique_tuple(tuple(kept))


def _artifact_ref(paths: WorkspacePaths, path: Path) -> str:
    try:
        return path.relative_to(paths.root).as_posix()
    except ValueError:
        return path.as_posix()


def _json_files(directory: Path) -> tuple[Path, ...]:
    if not directory.exists():
        return ()
    return tuple(sorted(path for path in directory.iterdir() if path.is_file() and path.suffix == ".json"))


def _unique_tuple(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


__all__ = [
    "built_in_blueprint_provider_registrations",
]
