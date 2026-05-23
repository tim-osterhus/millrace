"""Runtime effect handlers for the Blueprint Planning loop."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from shutil import copyfile
from typing import TYPE_CHECKING

from pydantic import ValidationError

from millrace_ai.contracts import (
    BlueprintCritiqueDocument,
    BlueprintDraftDocument,
    BlueprintEvaluationDocument,
    BlueprintManifestDocument,
    BlueprintPacketDocument,
    BlueprintPromotionRecord,
    BlueprintRepairDecisionDocument,
    StageResultEnvelope,
    TaskDocument,
    WorkItemKind,
)
from millrace_ai.errors import QueueStateError
from millrace_ai.workspace.blueprint_state import (
    blueprint_packet_path,
    enqueue_blueprint_draft,
    move_candidate_blueprint_packet,
    persist_blueprint_critique,
    persist_blueprint_evaluation,
    persist_blueprint_packet,
    persist_blueprint_promotion,
    read_active_blueprint_draft,
    read_blueprint_draft,
    read_blueprint_manifest,
    read_blueprint_packet,
    update_active_blueprint_draft,
    write_blueprint_manifest,
)
from millrace_ai.workspace.paths import WorkspacePaths
from millrace_ai.workspace.queue_transitions import enqueue_task
from millrace_ai.workspace.work_documents import read_work_document_as

from .artifact_contracts import (
    RuntimeArtifactError,
    parse_resolved_run_artifact_as,
    resolve_run_artifact,
)
from .effects import (
    RuntimeEffectDecision,
    RuntimeEffectMutationPhase,
    RuntimeEffectResult,
    SourceLifecycleAction,
    SourceLifecycleIntent,
)

if TYPE_CHECKING:
    from millrace_ai.architecture import CompiledRunPlan

MANAGER_BLUEPRINT_HANDLER_ID = "manager_blueprint_manifest_to_blueprint_drafts"
CONTRACTOR_BLUEPRINT_HANDLER_ID = "contractor_blueprint_candidate_persist"
EVALUATOR_BLUEPRINT_APPROVAL_HANDLER_ID = "evaluator_blueprint_approved_to_task"
EVALUATOR_BLUEPRINT_REJECTION_HANDLER_ID = "evaluator_blueprint_rejected_to_draft_revision"
MECHANIC_BLUEPRINT_REPAIR_HANDLER_ID = "mechanic_blueprint_repair_apply"


def manager_blueprint_manifest_to_blueprint_drafts(
    paths: WorkspacePaths,
    stage_result: StageResultEnvelope,
    run_dir: Path,
    compiled_plan: CompiledRunPlan | None = None,
) -> RuntimeEffectResult:
    """Promote Manager Blueprint manifest output into queued draft packets."""

    created_paths: list[str] = []
    try:
        manifest = _read_manager_json_model(
            run_dir / "blueprint_manifest.json",
            BlueprintManifestDocument,
            missing_class="blueprint_manifest_missing",
            parse_class="blueprint_manifest_parse_error",
            schema_class="blueprint_manifest_schema_invalid",
        )
        drafts = _read_manager_json_model_list(
            run_dir / "blueprint_drafts.json",
            BlueprintDraftDocument,
            missing_class="blueprint_drafts_missing",
            parse_class="blueprint_drafts_parse_error",
            schema_class="blueprint_drafts_schema_invalid",
        )
        _validate_manager_output(stage_result, manifest, drafts)
    except _ManagerBlueprintEffectError as exc:
        return _manager_failure_result(
            stage_result,
            failure_class=exc.failure_class,
            message=str(exc),
            created_paths=created_paths,
        )
    except ValueError as exc:
        return _manager_failure_result(
            stage_result,
            failure_class="blueprint_manifest_draft_mismatch",
            message=str(exc),
            created_paths=created_paths,
        )

    try:
        manifest_exists = _manager_manifest_exists_equivalent(paths, manifest)
        existing_draft_ids = {
            draft.draft_id
            for draft in drafts
            if _manager_draft_exists_equivalent(paths, draft)
        }
    except _ManagerBlueprintEffectError as exc:
        return _manager_failure_result(
            stage_result,
            failure_class=exc.failure_class,
            message=str(exc),
            created_paths=created_paths,
        )

    source_state = _manager_source_lifecycle_state(paths, stage_result)
    all_outputs_exist = manifest_exists and len(existing_draft_ids) == len(drafts)
    if all_outputs_exist:
        if source_state == "active":
            return _manager_success_result(
                created_paths=created_paths,
                source_lifecycle_intent=_source_lifecycle_intent(
                    stage_result,
                    plan_id=_complete_lifecycle_plan_id(stage_result.work_item_kind),
                    action=SourceLifecycleAction.COMPLETE,
                ),
                message=f"queued {len(drafts)} blueprint draft(s)",
            )
        if source_state == "target":
            return _manager_success_result(
                created_paths=created_paths,
                source_lifecycle_intent=None,
                message=f"blueprint draft output already exists for {manifest.manifest_id}",
            )
        return _manager_failure_result(
            stage_result,
            failure_class="blueprint_source_lifecycle_invalid",
            message=f"source work item is not active: {stage_result.work_item_id}",
            created_paths=created_paths,
            include_source_lifecycle_intent=False,
        )

    if source_state != "active":
        return _manager_failure_result(
            stage_result,
            failure_class="blueprint_source_lifecycle_invalid",
            message=f"source work item is not active: {stage_result.work_item_id}",
            created_paths=created_paths,
            include_source_lifecycle_intent=False,
        )

    try:
        if not manifest_exists:
            manifest_path = write_blueprint_manifest(paths, manifest)
            created_paths.append(_effect_path(paths, manifest_path))
        for draft in drafts:
            if draft.draft_id in existing_draft_ids:
                continue
            draft_path = enqueue_blueprint_draft(paths, draft)
            created_paths.append(_effect_path(paths, draft_path))
    except (OSError, QueueStateError) as exc:
        return _manager_failure_result(
            stage_result,
            failure_class=_manager_write_failure_class(exc, created_paths),
            message=str(exc),
            created_paths=created_paths,
        )

    return _manager_success_result(
        created_paths=created_paths,
        source_lifecycle_intent=_source_lifecycle_intent(
            stage_result,
            plan_id=_complete_lifecycle_plan_id(stage_result.work_item_kind),
            action=SourceLifecycleAction.COMPLETE,
        ),
        message=f"queued {len(drafts)} blueprint draft(s)",
    )


def contractor_blueprint_candidate_persist(
    paths: WorkspacePaths,
    stage_result: StageResultEnvelope,
    run_dir: Path,
    compiled_plan: CompiledRunPlan | None = None,
) -> RuntimeEffectResult:
    """Persist Contractor Blueprint candidate output before Evaluator routing."""

    created_paths: list[str] = []
    try:
        draft = _active_draft_for_stage_result(paths, stage_result)
        packet = _read_json_model(run_dir / "blueprint_packet.json", BlueprintPacketDocument)
        packet.ensure_matches_draft(draft)
        _validate_packet_critique_reference(draft, packet)

        packet_exists = _candidate_packet_exists_equivalent(paths, packet)
        markdown_exists = _candidate_markdown_exists_equivalent(
            paths,
            packet.blueprint_id,
            run_dir,
        )

        if not packet_exists:
            packet_path = persist_blueprint_packet(paths, packet, packet_state="candidates")
            created_paths.append(_effect_path(paths, packet_path))
        if not markdown_exists:
            markdown_path = _persist_candidate_markdown(paths, packet.blueprint_id, run_dir)
            created_paths.append(_effect_path(paths, markdown_path))

        updated = draft.model_copy(update={"latest_blueprint_id": packet.blueprint_id})
        update_active_blueprint_draft(paths, updated)

        return RuntimeEffectResult(
            handler_id=CONTRACTOR_BLUEPRINT_HANDLER_ID,
            decision=RuntimeEffectDecision.CONTINUE_ROUTE,
            created_paths=tuple(created_paths),
            message=(
                f"persisted candidate blueprint {packet.blueprint_id}"
                if created_paths
                else f"candidate blueprint {packet.blueprint_id} already persisted"
            ),
        )
    except _ContractorBlueprintEffectError as exc:
        return _failure_result(
            CONTRACTOR_BLUEPRINT_HANDLER_ID,
            stage_result,
            failure_class=exc.failure_class,
            message=str(exc),
            created_paths=created_paths,
        )
    except Exception as exc:
        return _failure_result(
            CONTRACTOR_BLUEPRINT_HANDLER_ID,
            stage_result,
            failure_class=(
                "blueprint_partial_mutation"
                if created_paths
                else "blueprint_candidate_invalid"
            ),
            message=str(exc),
            created_paths=created_paths,
        )


def evaluator_blueprint_approved_to_task(
    paths: WorkspacePaths,
    stage_result: StageResultEnvelope,
    run_dir: Path,
    compiled_plan: CompiledRunPlan | None = None,
) -> RuntimeEffectResult:
    """Promote an approved Blueprint candidate into an execution task."""

    created_paths: list[str] = []
    try:
        draft, source_state = _approval_draft_for_stage_result(paths, stage_result)
        packet = _approval_packet_for_draft(paths, draft)
        evaluation = _read_json_model(
            run_dir / "blueprint_evaluation.json",
            BlueprintEvaluationDocument,
        )
        _validate_approval(evaluation, packet)
        task = parse_resolved_run_artifact_as(
            resolve_run_artifact(compiled_plan, "generated_task", run_dir),
            TaskDocument,
        )
        _validate_generated_task(task, draft, packet)
        return _promote_approved_blueprint_task(
            paths,
            handler_id=EVALUATOR_BLUEPRINT_APPROVAL_HANDLER_ID,
            draft=draft,
            source_state=source_state,
            packet=packet,
            evaluation=evaluation,
            task=task,
            run_dir=run_dir,
            created_paths=created_paths,
        )
    except _ApprovalBlueprintEffectError as exc:
        return _failure_result(
            EVALUATOR_BLUEPRINT_APPROVAL_HANDLER_ID,
            stage_result,
            failure_class=exc.failure_class,
            message=str(exc),
            created_paths=created_paths,
        )
    except Exception as exc:
        failure_class = _approval_failure_class(exc, created_paths)
        return _failure_result(
            EVALUATOR_BLUEPRINT_APPROVAL_HANDLER_ID,
            stage_result,
            failure_class=failure_class,
            message=str(exc),
            created_paths=created_paths,
        )


def mechanic_blueprint_repair_apply(
    paths: WorkspacePaths,
    stage_result: StageResultEnvelope,
    run_dir: Path,
    compiled_plan: CompiledRunPlan | None = None,
) -> RuntimeEffectResult:
    """Apply a structured Mechanic Blueprint repair decision through runtime-owned mutation."""

    created_paths: list[str] = []
    try:
        decision = _read_blueprint_repair_decision(compiled_plan, run_dir)
        _validate_mechanic_repair_stage_result(decision, stage_result)
        if decision.repair_action == "block_for_operator":
            raise _RepairBlueprintEffectError(
                "blueprint_repair_requested_operator",
                decision.operator_reason or "Mechanic Blueprint requested operator review",
            )
        if decision.repair_action != "apply_repaired_generated_task":
            raise _RepairBlueprintEffectError(
                "blueprint_repair_context_mismatch",
                f"repair action {decision.repair_action} is not runtime-applied by this handler",
            )

        _failed_runtime_effect_context_for_decision(run_dir, decision)
        draft, source_state = _approval_draft_for_stage_result(paths, stage_result)
        _validate_repair_decision_matches_draft(decision, draft)
        packet = _approval_packet_for_draft(paths, draft)
        evaluation = _read_repair_evaluation(run_dir)
        _validate_repair_decision_matches_approval_context(
            decision,
            packet=packet,
            evaluation=evaluation,
        )
        task = _read_repaired_generated_task(compiled_plan, run_dir)
        _validate_repaired_generated_task(
            task,
            decision=decision,
            draft=draft,
            packet=packet,
        )

        return _promote_approved_blueprint_task(
            paths,
            handler_id=MECHANIC_BLUEPRINT_REPAIR_HANDLER_ID,
            draft=draft,
            source_state=source_state,
            packet=packet,
            evaluation=evaluation,
            task=task,
            run_dir=run_dir,
            created_paths=created_paths,
        )
    except _RepairBlueprintEffectError as exc:
        return _failure_result(
            MECHANIC_BLUEPRINT_REPAIR_HANDLER_ID,
            stage_result,
            failure_class=exc.failure_class,
            message=str(exc),
            created_paths=created_paths,
        )
    except _ApprovalBlueprintEffectError as exc:
        return _failure_result(
            MECHANIC_BLUEPRINT_REPAIR_HANDLER_ID,
            stage_result,
            failure_class=(
                "blueprint_partial_mutation"
                if created_paths
                else "blueprint_repair_context_mismatch"
            ),
            message=str(exc),
            created_paths=created_paths,
        )
    except Exception as exc:
        return _failure_result(
            MECHANIC_BLUEPRINT_REPAIR_HANDLER_ID,
            stage_result,
            failure_class=(
                "blueprint_partial_mutation"
                if created_paths
                else "blueprint_repair_context_mismatch"
            ),
            message=str(exc),
            created_paths=created_paths,
        )


def evaluator_blueprint_rejected_to_draft_revision(
    paths: WorkspacePaths,
    stage_result: StageResultEnvelope,
    run_dir: Path,
    compiled_plan: CompiledRunPlan | None = None,
) -> RuntimeEffectResult:
    """Persist Evaluator rejection output and keep the draft active for revision."""

    created_paths: list[str] = []
    try:
        draft = _active_draft_for_stage_result(paths, stage_result)
        packet = _candidate_packet_for_draft(paths, draft)
        evaluation = _read_json_model(
            run_dir / "blueprint_evaluation.json",
            BlueprintEvaluationDocument,
        )
        critique = parse_resolved_run_artifact_as(
            resolve_run_artifact(compiled_plan, "blueprint_critique", run_dir),
            BlueprintCritiqueDocument,
        )
        _validate_rejection(evaluation, critique, packet)

        evaluation_path = persist_blueprint_evaluation(paths, evaluation)
        created_paths.append(_effect_path(paths, evaluation_path))
        rejected_packet_path = move_candidate_blueprint_packet(
            paths,
            packet.blueprint_id,
            target_state="rejected",
        )
        created_paths.append(_effect_path(paths, rejected_packet_path))
        rejected_markdown_path = _move_candidate_markdown(
            paths,
            packet.blueprint_id,
            target_state="rejected",
        )
        if rejected_markdown_path is not None:
            created_paths.append(_effect_path(paths, rejected_markdown_path))
        critique_path = persist_blueprint_critique(paths, critique, critique_state="open")
        created_paths.append(_effect_path(paths, critique_path))

        updated = draft.model_copy(
            update={
                "current_revision": packet.revision,
                "latest_critique_id": critique.critique_id,
                "latest_blueprint_id": packet.blueprint_id,
                "updated_at": evaluation.created_at,
            }
        )
        update_active_blueprint_draft(paths, updated)

        return RuntimeEffectResult(
            handler_id=EVALUATOR_BLUEPRINT_REJECTION_HANDLER_ID,
            decision=RuntimeEffectDecision.CONTINUE_ROUTE,
            created_paths=tuple(created_paths),
            message=f"recorded rejection critique {critique.critique_id}",
        )
    except Exception as exc:
        return _failure_result(
            EVALUATOR_BLUEPRINT_REJECTION_HANDLER_ID,
            stage_result,
            failure_class=(
                "blueprint_partial_mutation"
                if created_paths
                else "blueprint_critique_invalid"
            ),
            message=str(exc),
            created_paths=created_paths,
        )


@dataclass(frozen=True, slots=True)
class _ManagerBlueprintEffectError(Exception):
    failure_class: str
    message: str

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True, slots=True)
class _ContractorBlueprintEffectError(Exception):
    failure_class: str
    message: str

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True, slots=True)
class _ApprovalBlueprintEffectError(Exception):
    failure_class: str
    message: str

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True, slots=True)
class _RepairBlueprintEffectError(Exception):
    failure_class: str
    message: str

    def __str__(self) -> str:
        return self.message


_DRAFT_STATES: tuple[tuple[str, str], ...] = (
    ("queue", "queued"),
    ("active", "active"),
    ("approved", "approved"),
    ("blocked", "blocked"),
    ("canceled", "canceled"),
    ("superseded", "superseded"),
)


def _read_manager_json_model(
    path: Path,
    model: type[BlueprintModelT],
    *,
    missing_class: str,
    parse_class: str,
    schema_class: str,
) -> BlueprintModelT:
    payload = _read_manager_json_payload(
        path,
        missing_class=missing_class,
        parse_class=parse_class,
    )
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        raise _ManagerBlueprintEffectError(
            schema_class,
            f"{path.name} failed schema validation: {exc}",
        ) from exc


def _read_manager_json_model_list(
    path: Path,
    model: type[BlueprintModelT],
    *,
    missing_class: str,
    parse_class: str,
    schema_class: str,
) -> tuple[BlueprintModelT, ...]:
    payload = _read_manager_json_payload(
        path,
        missing_class=missing_class,
        parse_class=parse_class,
    )
    if not isinstance(payload, list):
        raise _ManagerBlueprintEffectError(
            schema_class,
            f"{path.name} must be a JSON list",
        )
    try:
        return tuple(model.model_validate(item) for item in payload)
    except ValidationError as exc:
        raise _ManagerBlueprintEffectError(
            schema_class,
            f"{path.name} failed schema validation: {exc}",
        ) from exc


def _read_manager_json_payload(
    path: Path,
    *,
    missing_class: str,
    parse_class: str,
) -> object:
    if not path.exists():
        raise _ManagerBlueprintEffectError(
            missing_class,
            f"required Blueprint artifact is missing: {path.name}",
        )
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise _ManagerBlueprintEffectError(
            missing_class,
            f"required Blueprint artifact could not be read: {path.name}: {exc}",
        ) from exc
    except json.JSONDecodeError as exc:
        raise _ManagerBlueprintEffectError(
            parse_class,
            f"{path.name} is not valid JSON: {exc}",
        ) from exc


def _manager_manifest_exists_equivalent(
    paths: WorkspacePaths,
    manifest: BlueprintManifestDocument,
) -> bool:
    try:
        existing = read_blueprint_manifest(paths, manifest.manifest_id)
    except QueueStateError as exc:
        message = str(exc)
        if "blueprint_manifest_missing" in message:
            return False
        if "blueprint_manifest_duplicate" in message:
            raise _ManagerBlueprintEffectError("blueprint_manifest_duplicate", message) from exc
        raise _ManagerBlueprintEffectError("blueprint_manifest_duplicate", message) from exc
    except (OSError, ValueError) as exc:
        raise _ManagerBlueprintEffectError(
            "blueprint_manifest_duplicate",
            f"existing manifest {manifest.manifest_id} cannot be validated: {exc}",
        ) from exc

    if _normalized_blueprint_model_payload(existing) != _normalized_blueprint_model_payload(
        manifest,
    ):
        raise _ManagerBlueprintEffectError(
            "blueprint_manifest_duplicate",
            f"blueprint_manifest_duplicate: manifest_id={manifest.manifest_id}",
        )
    return True


def _manager_draft_exists_equivalent(
    paths: WorkspacePaths,
    draft: BlueprintDraftDocument,
) -> bool:
    entries: list[Path] = []
    for state, _status in _DRAFT_STATES:
        path = paths.runtime_root / "blueprints" / "drafts" / state / f"{draft.draft_id}.json"
        if not path.exists():
            continue
        try:
            existing = read_blueprint_draft(path)
        except (OSError, ValueError) as exc:
            raise _ManagerBlueprintEffectError(
                "blueprint_draft_duplicate",
                f"existing draft {draft.draft_id} cannot be validated: {exc}",
            ) from exc
        if _manager_draft_identity_payload(existing) != _manager_draft_identity_payload(draft):
            raise _ManagerBlueprintEffectError(
                "blueprint_draft_duplicate",
                f"blueprint_draft_duplicate: draft_id={draft.draft_id}",
            )
        entries.append(path)
    if len(entries) > 1:
        locations = ", ".join(path.as_posix() for path in entries)
        raise _ManagerBlueprintEffectError(
            "blueprint_draft_duplicate",
            f"blueprint_draft_duplicate: draft_id={draft.draft_id} in {locations}",
        )
    return bool(entries)


def _manager_source_lifecycle_state(
    paths: WorkspacePaths,
    stage_result: StageResultEnvelope,
) -> str:
    if stage_result.work_item_kind is WorkItemKind.SPEC:
        active = paths.specs_active_dir / f"{stage_result.work_item_id}.md"
        target = paths.specs_done_dir / f"{stage_result.work_item_id}.md"
    elif stage_result.work_item_kind is WorkItemKind.INCIDENT:
        active = paths.incidents_active_dir / f"{stage_result.work_item_id}.md"
        target = paths.incidents_resolved_dir / f"{stage_result.work_item_id}.md"
    else:
        return "invalid"
    if active.exists():
        return "active"
    if target.exists():
        return "target"
    return "invalid"


def _manager_success_result(
    *,
    created_paths: Sequence[str],
    source_lifecycle_intent: SourceLifecycleIntent | None,
    message: str,
) -> RuntimeEffectResult:
    return RuntimeEffectResult(
        handler_id=MANAGER_BLUEPRINT_HANDLER_ID,
        decision=RuntimeEffectDecision.REQUEST_COMPLETE_SOURCE,
        created_paths=tuple(created_paths),
        source_lifecycle_intent=source_lifecycle_intent,
        message=message,
    )


def _manager_failure_result(
    stage_result: StageResultEnvelope,
    *,
    failure_class: str,
    message: str,
    created_paths: Sequence[str],
    include_source_lifecycle_intent: bool = True,
) -> RuntimeEffectResult:
    return RuntimeEffectResult(
        handler_id=MANAGER_BLUEPRINT_HANDLER_ID,
        decision=RuntimeEffectDecision.REQUEST_BLOCK_SOURCE,
        created_paths=tuple(created_paths),
        source_lifecycle_intent=(
            _source_lifecycle_intent(
                stage_result,
                plan_id=_block_lifecycle_plan_id(stage_result.work_item_kind),
                action=SourceLifecycleAction.BLOCK,
            )
            if include_source_lifecycle_intent
            else None
        ),
        failure_class=failure_class,
        message=message,
        mutation_phase=(
            RuntimeEffectMutationPhase.PARTIAL_MUTATION
            if created_paths
            else RuntimeEffectMutationPhase.PRE_MUTATION
        ),
    )


def _manager_write_failure_class(
    exc: Exception,
    created_paths: Sequence[str],
) -> str:
    if created_paths:
        return "blueprint_partial_mutation"
    message = str(exc)
    if "blueprint_manifest_duplicate" in message:
        return "blueprint_manifest_duplicate"
    if "blueprint draft" in message and "already exists" in message:
        return "blueprint_draft_duplicate"
    if "Blueprint artifact already exists" in message and "manifests" in message:
        return "blueprint_manifest_duplicate"
    return "blueprint_partial_mutation"


def _normalized_blueprint_model_payload(document: BlueprintModelT) -> str:
    return json.dumps(
        document.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    )


def _manager_draft_identity_payload(draft: BlueprintDraftDocument) -> str:
    return json.dumps(
        draft.model_dump(
            mode="json",
            exclude={
                "status",
                "current_revision",
                "latest_blueprint_id",
                "latest_critique_id",
                "updated_at",
            },
        ),
        sort_keys=True,
        separators=(",", ":"),
    )


def _read_json_model(path: Path, model: type[BlueprintModelT]) -> BlueprintModelT:
    if not path.exists():
        raise QueueStateError(f"required Blueprint artifact is missing: {path.name}")
    return model.model_validate_json(path.read_text(encoding="utf-8"))


def _read_json_model_list(
    path: Path,
    model: type[BlueprintModelT],
) -> tuple[BlueprintModelT, ...]:
    if not path.exists():
        raise QueueStateError(f"required Blueprint artifact is missing: {path.name}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise QueueStateError(f"Blueprint artifact {path.name} must be a JSON list")
    return tuple(model.model_validate(item) for item in payload)


def _validate_manager_output(
    stage_result: StageResultEnvelope,
    manifest: BlueprintManifestDocument,
    drafts: Sequence[BlueprintDraftDocument],
) -> None:
    if manifest.source_work_item_kind != stage_result.work_item_kind.value:
        raise ValueError("manifest source_work_item_kind does not match active source")
    if manifest.source_work_item_id != stage_result.work_item_id:
        raise ValueError("manifest source_work_item_id does not match active source")
    if tuple(draft.draft_id for draft in drafts) != manifest.draft_ids:
        raise ValueError("draft order must match manifest draft_ids")
    if len(drafts) != manifest.draft_count:
        raise ValueError("draft count must match manifest")

    expected_indexes = tuple(range(1, len(drafts) + 1))
    actual_indexes = tuple(draft.draft_index for draft in drafts)
    if actual_indexes != expected_indexes:
        raise ValueError("draft indexes must be contiguous starting at 1")

    previous_ids: set[str] = set()
    previous_id: str | None = None
    for draft in drafts:
        _ensure_draft_matches_manifest(draft, manifest)
        if draft.draft_index == 1 and draft.depends_on_draft_ids:
            raise ValueError("first Blueprint draft cannot declare dependencies")
        if draft.draft_index > 1 and draft.depends_on_draft_ids != (previous_id,):
            raise ValueError("strict Blueprint sequence requires dependency on previous draft")
        if not set(draft.depends_on_draft_ids).issubset(previous_ids):
            raise ValueError("Blueprint draft dependencies must refer to earlier drafts")
        previous_ids.add(draft.draft_id)
        previous_id = draft.draft_id


def _ensure_draft_matches_manifest(
    draft: BlueprintDraftDocument,
    manifest: BlueprintManifestDocument,
) -> None:
    for field_name in ("manifest_id", "root_spec_id", "root_idea_id", "source_spec_id"):
        if getattr(draft, field_name) != getattr(manifest, field_name):
            raise ValueError(f"draft {field_name} does not match manifest")


def _active_draft_for_stage_result(
    paths: WorkspacePaths,
    stage_result: StageResultEnvelope,
) -> BlueprintDraftDocument:
    if stage_result.work_item_kind is not WorkItemKind.BLUEPRINT_DRAFT:
        raise QueueStateError(
            f"Blueprint handler requires blueprint_draft source, got {stage_result.work_item_kind.value}"
        )
    return read_active_blueprint_draft(paths, stage_result.work_item_id)


def _validate_packet_critique_reference(
    draft: BlueprintDraftDocument,
    packet: BlueprintPacketDocument,
) -> None:
    if draft.latest_critique_id is None:
        return
    if not any(draft.latest_critique_id in reference for reference in packet.references):
        raise ValueError("candidate Blueprint must reference latest open critique")


def _candidate_packet_exists_equivalent(
    paths: WorkspacePaths,
    packet: BlueprintPacketDocument,
) -> bool:
    path = blueprint_packet_path(paths, packet.blueprint_id, packet_state="candidates")
    if not path.exists():
        return False
    try:
        existing = read_blueprint_packet(
            paths,
            packet.blueprint_id,
            packet_state="candidates",
        )
    except (OSError, ValueError, ValidationError) as exc:
        raise _ContractorBlueprintEffectError(
            "blueprint_candidate_duplicate_conflict",
            f"existing candidate packet {packet.blueprint_id} cannot be validated: {exc}",
        ) from exc
    if _normalized_blueprint_model_payload(existing) != _normalized_blueprint_model_payload(
        packet,
    ):
        raise _ContractorBlueprintEffectError(
            "blueprint_candidate_duplicate_conflict",
            f"blueprint_candidate_duplicate_conflict: blueprint_id={packet.blueprint_id}",
        )
    return True


def _candidate_markdown_exists_equivalent(
    paths: WorkspacePaths,
    blueprint_id: str,
    run_dir: Path,
) -> bool:
    source = _candidate_markdown_source(run_dir)
    destination = _candidate_markdown_path(paths, blueprint_id)
    if not destination.exists():
        return False
    try:
        source_content = source.read_text(encoding="utf-8")
        existing_content = destination.read_text(encoding="utf-8")
    except OSError as exc:
        raise _ContractorBlueprintEffectError(
            "blueprint_candidate_markdown_conflict",
            f"existing candidate markdown {blueprint_id} cannot be validated: {exc}",
        ) from exc
    if _normalized_markdown_content(existing_content) != _normalized_markdown_content(
        source_content,
    ):
        raise _ContractorBlueprintEffectError(
            "blueprint_candidate_markdown_conflict",
            f"blueprint_candidate_markdown_conflict: blueprint_id={blueprint_id}",
        )
    return True


def _persist_candidate_markdown(
    paths: WorkspacePaths,
    blueprint_id: str,
    run_dir: Path,
) -> Path:
    source = _candidate_markdown_source(run_dir)
    destination = _candidate_markdown_path(paths, blueprint_id)
    _copy_unique_file(source, destination)
    return destination


def _candidate_markdown_source(run_dir: Path) -> Path:
    source = run_dir / "blueprint.md"
    if not source.exists():
        raise QueueStateError("required Blueprint artifact is missing: blueprint.md")
    return source


def _candidate_markdown_path(paths: WorkspacePaths, blueprint_id: str) -> Path:
    return paths.runtime_root / "blueprints" / "packets" / "candidates" / f"{blueprint_id}.md"


def _normalized_markdown_content(content: str) -> str:
    return content.replace("\r\n", "\n").replace("\r", "\n")


def _candidate_packet_for_draft(
    paths: WorkspacePaths,
    draft: BlueprintDraftDocument,
) -> BlueprintPacketDocument:
    if draft.latest_blueprint_id is None:
        raise QueueStateError("active draft has no latest candidate blueprint")
    path = (
        paths.runtime_root
        / "blueprints"
        / "packets"
        / "candidates"
        / f"{draft.latest_blueprint_id}.json"
    )
    packet = _read_json_model(path, BlueprintPacketDocument)
    packet.ensure_matches_draft(draft)
    return packet


def _approval_draft_for_stage_result(
    paths: WorkspacePaths,
    stage_result: StageResultEnvelope,
) -> tuple[BlueprintDraftDocument, str]:
    if stage_result.work_item_kind is not WorkItemKind.BLUEPRINT_DRAFT:
        raise QueueStateError(
            f"Blueprint handler requires blueprint_draft source, got {stage_result.work_item_kind.value}"
        )
    entries: list[tuple[str, BlueprintDraftDocument]] = []
    for state in ("active", "approved"):
        path = paths.runtime_root / "blueprints" / "drafts" / state / f"{stage_result.work_item_id}.json"
        if path.exists():
            entries.append((state, read_blueprint_draft(path)))
    if len(entries) > 1:
        raise QueueStateError(f"blueprint draft {stage_result.work_item_id} exists in active and approved")
    if not entries:
        raise QueueStateError(f"active or approved blueprint draft {stage_result.work_item_id} not found")
    return entries[0][1], entries[0][0]


def _approval_packet_for_draft(
    paths: WorkspacePaths,
    draft: BlueprintDraftDocument,
) -> BlueprintPacketDocument:
    if draft.latest_blueprint_id is None:
        raise QueueStateError("active draft has no latest candidate blueprint")
    entries: list[tuple[str, BlueprintPacketDocument]] = []
    for state in ("candidates", "approved"):
        path = blueprint_packet_path(
            paths,
            draft.latest_blueprint_id,
            packet_state=state,
        )
        if not path.exists():
            continue
        try:
            packet = read_blueprint_packet(
                paths,
                draft.latest_blueprint_id,
                packet_state=state,
            )
            packet.ensure_matches_draft(draft)
        except (OSError, ValueError, ValidationError) as exc:
            raise _ApprovalBlueprintEffectError(
                "blueprint_approved_packet_conflict",
                f"existing {state} packet {draft.latest_blueprint_id} cannot be validated: {exc}",
            ) from exc
        entries.append((state, packet))
    if not entries:
        raise QueueStateError(f"candidate or approved blueprint packet {draft.latest_blueprint_id} not found")
    expected_payload = _normalized_blueprint_model_payload(entries[0][1])
    if any(_normalized_blueprint_model_payload(packet) != expected_payload for _state, packet in entries[1:]):
        raise _ApprovalBlueprintEffectError(
            "blueprint_approved_packet_conflict",
            f"blueprint_approved_packet_conflict: blueprint_id={draft.latest_blueprint_id}",
        )
    return entries[0][1]


def _validate_approval(
    evaluation: BlueprintEvaluationDocument,
    packet: BlueprintPacketDocument,
) -> None:
    if evaluation.decision != "approved":
        raise ValueError("approval handler requires decision=approved")
    evaluation.ensure_matches_packet(packet)


def _validate_rejection(
    evaluation: BlueprintEvaluationDocument,
    critique: BlueprintCritiqueDocument,
    packet: BlueprintPacketDocument,
) -> None:
    if evaluation.decision != "rejected":
        raise ValueError("rejection handler requires decision=rejected")
    evaluation.ensure_matches_packet(packet)
    critique.ensure_matches_packet(packet)
    if evaluation.critique_id != critique.critique_id:
        raise ValueError("evaluation critique_id must match critique packet")


def _read_blueprint_repair_decision(
    compiled_plan: CompiledRunPlan | None,
    run_dir: Path,
) -> BlueprintRepairDecisionDocument:
    try:
        return parse_resolved_run_artifact_as(
            resolve_run_artifact(compiled_plan, "blueprint_repair_decision", run_dir),
            BlueprintRepairDecisionDocument,
        )
    except RuntimeArtifactError as exc:
        failure_class = (
            "blueprint_repair_decision_missing"
            if exc.failure_class == "artifact_missing"
            else "blueprint_repair_decision_invalid"
        )
        raise _RepairBlueprintEffectError(failure_class, str(exc)) from exc
    except (OSError, ValueError, ValidationError) as exc:
        raise _RepairBlueprintEffectError(
            "blueprint_repair_decision_invalid",
            str(exc),
        ) from exc


def _read_repaired_generated_task(
    compiled_plan: CompiledRunPlan | None,
    run_dir: Path,
) -> TaskDocument:
    try:
        return parse_resolved_run_artifact_as(
            resolve_run_artifact(compiled_plan, "repaired_generated_task", run_dir),
            TaskDocument,
        )
    except RuntimeArtifactError as exc:
        failure_class = (
            "blueprint_repaired_generated_task_missing"
            if exc.failure_class == "artifact_missing"
            else "blueprint_repaired_generated_task_invalid"
        )
        raise _RepairBlueprintEffectError(failure_class, str(exc)) from exc
    except (OSError, ValueError, ValidationError) as exc:
        raise _RepairBlueprintEffectError(
            "blueprint_repaired_generated_task_invalid",
            str(exc),
        ) from exc


def _read_repair_evaluation(run_dir: Path) -> BlueprintEvaluationDocument:
    try:
        return _read_json_model(run_dir / "blueprint_evaluation.json", BlueprintEvaluationDocument)
    except (OSError, ValueError, ValidationError, QueueStateError) as exc:
        raise _RepairBlueprintEffectError(
            "blueprint_repair_context_mismatch",
            f"failed approval evaluation cannot be verified: {exc}",
        ) from exc


def _validate_mechanic_repair_stage_result(
    decision: BlueprintRepairDecisionDocument,
    stage_result: StageResultEnvelope,
) -> None:
    _ensure_repair_context_equal(
        "stage_kind_id",
        stage_result.stage_kind_id,
        "mechanic_blueprint",
    )
    _ensure_repair_context_equal(
        "terminal_result",
        stage_result.terminal_result.value,
        "MECHANIC_BLUEPRINT_COMPLETE",
    )
    _ensure_repair_context_equal(
        "work_item_family_id",
        stage_result.work_item_family_id,
        decision.work_item_family_id,
    )
    _ensure_repair_context_equal("work_item_id", stage_result.work_item_id, decision.work_item_id)


def _failed_runtime_effect_context_for_decision(
    run_dir: Path,
    decision: BlueprintRepairDecisionDocument,
) -> StageResultEnvelope:
    stage_results_dir = run_dir / "stage_results"
    for path in sorted(stage_results_dir.glob("*.json")):
        try:
            stage_result = StageResultEnvelope.model_validate_json(
                path.read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            continue
        if _stage_result_matches_repair_decision(stage_result, decision):
            return stage_result
    raise _RepairBlueprintEffectError(
        "blueprint_repair_context_mismatch",
        "no failed runtime-effect stage result matches blueprint_repair_decision.json",
    )


def _stage_result_matches_repair_decision(
    stage_result: StageResultEnvelope,
    decision: BlueprintRepairDecisionDocument,
) -> bool:
    metadata = stage_result.metadata
    return (
        stage_result.run_id == decision.failed_run_id
        and stage_result.stage_kind_id == decision.failed_stage_kind_id
        and stage_result.node_id == decision.failed_node_id
        and stage_result.terminal_result.value == decision.failed_terminal_result
        and stage_result.work_item_family_id == decision.work_item_family_id
        and stage_result.work_item_id == decision.work_item_id
        and metadata.get("runtime_effect_handler_id") == decision.failed_handler_id
        and metadata.get("runtime_effect_decision") == "request_block_source"
        and metadata.get("runtime_effect_failure_class") == decision.failure_class
        and metadata.get("runtime_effect_mutation_phase") == decision.mutation_phase
    )


def _validate_repair_decision_matches_draft(
    decision: BlueprintRepairDecisionDocument,
    draft: BlueprintDraftDocument,
) -> None:
    _ensure_repair_context_equal("work_item_id", decision.work_item_id, draft.draft_id)
    _ensure_repair_context_equal("draft_id", decision.draft_id, draft.draft_id)
    _ensure_repair_context_equal("manifest_id", decision.manifest_id, draft.manifest_id)
    _ensure_repair_context_equal("root_spec_id", decision.root_spec_id, draft.root_spec_id)
    _ensure_repair_context_equal("root_idea_id", decision.root_idea_id, draft.root_idea_id)


def _validate_repair_decision_matches_approval_context(
    decision: BlueprintRepairDecisionDocument,
    *,
    packet: BlueprintPacketDocument,
    evaluation: BlueprintEvaluationDocument,
) -> None:
    try:
        decision.ensure_matches_packet(packet)
        decision.ensure_matches_evaluation(evaluation)
    except ValueError as exc:
        raise _RepairBlueprintEffectError(
            "blueprint_repair_context_mismatch",
            str(exc),
        ) from exc


def _validate_repaired_generated_task(
    task: TaskDocument,
    *,
    decision: BlueprintRepairDecisionDocument,
    draft: BlueprintDraftDocument,
    packet: BlueprintPacketDocument,
) -> None:
    try:
        decision.ensure_matches_repaired_generated_task(task)
        _validate_generated_task(task, draft, packet)
    except ValueError as exc:
        raise _RepairBlueprintEffectError(
            "blueprint_repaired_generated_task_invalid",
            str(exc),
        ) from exc


def _ensure_repair_context_equal(field_name: str, actual: object, expected: object) -> None:
    if actual != expected:
        raise _RepairBlueprintEffectError(
            "blueprint_repair_context_mismatch",
            f"{field_name} mismatch",
        )


def _validate_generated_task(
    task: TaskDocument,
    draft: BlueprintDraftDocument,
    packet: BlueprintPacketDocument,
) -> None:
    if task.root_spec_id != draft.root_spec_id:
        raise ValueError("generated task root_spec_id must match Blueprint draft")
    if task.root_idea_id != draft.root_idea_id:
        raise ValueError("generated task root_idea_id must match Blueprint draft")
    if task.spec_id != draft.source_spec_id:
        raise ValueError("generated task spec_id must match Blueprint source spec")
    _ensure_contains_all(
        task.acceptance,
        packet.task_acceptance,
        field_name="generated task acceptance",
    )
    _ensure_contains_all(
        task.required_checks,
        packet.required_checks,
        field_name="generated task required checks",
    )
    allowed_targets = set(packet.intended_files) | set(draft.target_paths)
    if not set(task.target_paths).issubset(allowed_targets):
        raise ValueError("generated task target_paths must stay within Blueprint scope")


def _blueprint_evaluation_path(paths: WorkspacePaths, evaluation_id: str) -> Path:
    return paths.runtime_root / "blueprints" / "evaluations" / f"{evaluation_id}.json"


def _blueprint_promotion_path(paths: WorkspacePaths, promotion_id: str) -> Path:
    return paths.runtime_root / "blueprints" / "promotions" / f"{promotion_id}.json"


def _approved_markdown_path(paths: WorkspacePaths, blueprint_id: str) -> Path:
    return paths.runtime_root / "blueprints" / "packets" / "approved" / f"{blueprint_id}.md"


def _blueprint_evaluation_exists_equivalent(
    paths: WorkspacePaths,
    evaluation: BlueprintEvaluationDocument,
) -> bool:
    path = _blueprint_evaluation_path(paths, evaluation.evaluation_id)
    if not path.exists():
        return False
    try:
        existing = _read_json_model(path, BlueprintEvaluationDocument)
    except (OSError, ValueError, ValidationError) as exc:
        raise _ApprovalBlueprintEffectError(
            "blueprint_evaluation_duplicate_conflict",
            f"existing evaluation {evaluation.evaluation_id} cannot be validated: {exc}",
        ) from exc
    if _normalized_blueprint_model_payload(existing) != _normalized_blueprint_model_payload(evaluation):
        raise _ApprovalBlueprintEffectError(
            "blueprint_evaluation_duplicate_conflict",
            f"blueprint_evaluation_duplicate_conflict: evaluation_id={evaluation.evaluation_id}",
        )
    return True


def _approved_packet_exists_equivalent(
    paths: WorkspacePaths,
    packet: BlueprintPacketDocument,
) -> bool:
    path = blueprint_packet_path(paths, packet.blueprint_id, packet_state="approved")
    if not path.exists():
        return False
    try:
        existing = read_blueprint_packet(paths, packet.blueprint_id, packet_state="approved")
    except (OSError, ValueError, ValidationError) as exc:
        raise _ApprovalBlueprintEffectError(
            "blueprint_approved_packet_conflict",
            f"existing approved packet {packet.blueprint_id} cannot be validated: {exc}",
        ) from exc
    if _normalized_blueprint_model_payload(existing) != _normalized_blueprint_model_payload(packet):
        raise _ApprovalBlueprintEffectError(
            "blueprint_approved_packet_conflict",
            f"blueprint_approved_packet_conflict: blueprint_id={packet.blueprint_id}",
        )
    return True


def _approved_markdown_exists_equivalent(
    paths: WorkspacePaths,
    blueprint_id: str,
    *,
    expected_content: str | None,
) -> bool:
    destination = _approved_markdown_path(paths, blueprint_id)
    if not destination.exists():
        return False
    try:
        destination_content = destination.read_text(encoding="utf-8")
    except OSError as exc:
        raise _ApprovalBlueprintEffectError(
            "blueprint_approved_markdown_conflict",
            f"existing approved markdown {blueprint_id} cannot be validated: {exc}",
        ) from exc
    if expected_content is None:
        raise _ApprovalBlueprintEffectError(
            "blueprint_approved_markdown_conflict",
            f"approved markdown {blueprint_id} cannot be verified without an expected source",
        )
    if _normalized_markdown_content(
        destination_content,
    ) != _normalized_markdown_content(expected_content):
        raise _ApprovalBlueprintEffectError(
            "blueprint_approved_markdown_conflict",
            f"blueprint_approved_markdown_conflict: blueprint_id={blueprint_id}",
        )
    return True


def _expected_approved_markdown_content(
    paths: WorkspacePaths,
    blueprint_id: str,
    run_dir: Path,
) -> str | None:
    for source in (_candidate_markdown_path(paths, blueprint_id), run_dir / "blueprint.md"):
        if not source.exists():
            continue
        try:
            return source.read_text(encoding="utf-8")
        except OSError as exc:
            raise _ApprovalBlueprintEffectError(
                "blueprint_approved_markdown_conflict",
                f"expected approved markdown {blueprint_id} cannot be read: {exc}",
            ) from exc
    return None


def _generated_task_exists_equivalent(paths: WorkspacePaths, task: TaskDocument) -> bool:
    entries: list[tuple[Path, TaskDocument]] = []
    filename = f"{task.task_id}.md"
    for directory in (
        paths.tasks_queue_dir,
        paths.tasks_active_dir,
        paths.tasks_done_dir,
        paths.tasks_blocked_dir,
    ):
        path = directory / filename
        if not path.exists():
            continue
        try:
            existing = read_work_document_as(path, model=TaskDocument)
        except (OSError, ValueError, ValidationError) as exc:
            raise _ApprovalBlueprintEffectError(
                "blueprint_task_duplicate",
                f"existing generated task {task.task_id} cannot be validated: {exc}",
            ) from exc
        entries.append((path, existing))
    if not entries:
        return False
    if len(entries) > 1:
        locations = ", ".join(path.as_posix() for path, _task in entries)
        raise _ApprovalBlueprintEffectError(
            "blueprint_task_duplicate",
            f"blueprint_task_duplicate: task_id={task.task_id} in {locations}",
        )
    existing = entries[0][1]
    if _normalized_task_payload(existing) != _normalized_task_payload(task):
        raise _ApprovalBlueprintEffectError(
            "blueprint_task_duplicate",
            f"blueprint_task_duplicate: task_id={task.task_id}",
        )
    return True


def _promote_approved_blueprint_task(
    paths: WorkspacePaths,
    *,
    handler_id: str,
    draft: BlueprintDraftDocument,
    source_state: str,
    packet: BlueprintPacketDocument,
    evaluation: BlueprintEvaluationDocument,
    task: TaskDocument,
    run_dir: Path,
    created_paths: list[str],
) -> RuntimeEffectResult:
    evaluation_path = _blueprint_evaluation_path(paths, evaluation.evaluation_id)
    approved_packet_path = blueprint_packet_path(
        paths,
        packet.blueprint_id,
        packet_state="approved",
    )
    expected_markdown_content = _expected_approved_markdown_content(
        paths,
        packet.blueprint_id,
        run_dir,
    )
    approved_markdown_exists = _approved_markdown_exists_equivalent(
        paths,
        packet.blueprint_id,
        expected_content=expected_markdown_content,
    )
    if not approved_markdown_exists and not _candidate_markdown_path(
        paths,
        packet.blueprint_id,
    ).exists():
        raise _ApprovalBlueprintEffectError(
            "blueprint_approved_markdown_conflict",
            f"approved markdown {packet.blueprint_id} cannot be verified or reconstructed",
        )
    task = _task_with_blueprint_refs(
        task,
        paths=paths,
        approved_blueprint_path=approved_packet_path,
        evaluation_path=evaluation_path,
    )
    task_path = paths.tasks_queue_dir / f"{task.task_id}.md"
    promotion = _promotion_record_for_approval(
        paths,
        draft=draft,
        packet=packet,
        evaluation=evaluation,
        task=task,
        task_path=task_path,
        approved_packet_path=approved_packet_path,
        evaluation_path=evaluation_path,
    )

    evaluation_exists = _blueprint_evaluation_exists_equivalent(
        paths,
        evaluation,
    )
    approved_packet_exists = _approved_packet_exists_equivalent(paths, packet)
    task_exists = _generated_task_exists_equivalent(paths, task)
    promotion_exists = _promotion_exists_equivalent(paths, promotion)

    if not evaluation_exists:
        evaluation_path = persist_blueprint_evaluation(paths, evaluation)
        created_paths.append(_effect_path(paths, evaluation_path))
    if not approved_packet_exists:
        approved_packet_path = move_candidate_blueprint_packet(
            paths,
            packet.blueprint_id,
            target_state="approved",
        )
        created_paths.append(_effect_path(paths, approved_packet_path))
    if not approved_markdown_exists:
        approved_markdown_path = _move_candidate_markdown(
            paths,
            packet.blueprint_id,
            target_state="approved",
        )
        if approved_markdown_path is not None:
            created_paths.append(_effect_path(paths, approved_markdown_path))
        if not _approved_markdown_exists_equivalent(
            paths,
            packet.blueprint_id,
            expected_content=expected_markdown_content,
        ):
            raise _ApprovalBlueprintEffectError(
                "blueprint_approved_markdown_conflict",
                f"approved markdown {packet.blueprint_id} cannot be verified or reconstructed",
            )
    if not task_exists:
        task_path = enqueue_task(paths, task)
        created_paths.append(_effect_path(paths, task_path))
    if not promotion_exists:
        promotion_path = persist_blueprint_promotion(paths, promotion)
        created_paths.append(_effect_path(paths, promotion_path))

    return RuntimeEffectResult(
        handler_id=handler_id,
        decision=RuntimeEffectDecision.REQUEST_COMPLETE_SOURCE,
        created_paths=tuple(created_paths),
        source_lifecycle_intent=(
            SourceLifecycleIntent(
                lifecycle_plan_id="approve_blueprint_draft_after_effect",
                action=SourceLifecycleAction.COMPLETE,
                work_item_kind=WorkItemKind.BLUEPRINT_DRAFT,
                work_item_id=draft.draft_id,
            )
            if source_state == "active"
            else None
        ),
        message=f"promoted blueprint {packet.blueprint_id} to task {task.task_id}",
    )


def _promotion_record_for_approval(
    paths: WorkspacePaths,
    *,
    draft: BlueprintDraftDocument,
    packet: BlueprintPacketDocument,
    evaluation: BlueprintEvaluationDocument,
    task: TaskDocument,
    task_path: Path,
    approved_packet_path: Path,
    evaluation_path: Path,
) -> BlueprintPromotionRecord:
    return BlueprintPromotionRecord(
        promotion_id=_promotion_id(evaluation.evaluation_id),
        blueprint_id=packet.blueprint_id,
        evaluation_id=evaluation.evaluation_id,
        draft_id=draft.draft_id,
        manifest_id=draft.manifest_id,
        root_spec_id=draft.root_spec_id,
        root_idea_id=draft.root_idea_id,
        generated_task_id=task.task_id,
        generated_task_path=_effect_path(paths, task_path),
        approved_blueprint_path=_effect_path(paths, approved_packet_path),
        evaluation_path=_effect_path(paths, evaluation_path),
        promoted_at=evaluation.created_at,
    )


def _promotion_exists_equivalent(
    paths: WorkspacePaths,
    promotion: BlueprintPromotionRecord,
) -> bool:
    path = _blueprint_promotion_path(paths, promotion.promotion_id)
    if not path.exists():
        return False
    try:
        existing = _read_json_model(path, BlueprintPromotionRecord)
    except (OSError, ValueError, ValidationError) as exc:
        raise _ApprovalBlueprintEffectError(
            "blueprint_promotion_duplicate_conflict",
            f"existing promotion {promotion.promotion_id} cannot be validated: {exc}",
        ) from exc
    if _normalized_blueprint_model_payload(existing) != _normalized_blueprint_model_payload(promotion):
        raise _ApprovalBlueprintEffectError(
            "blueprint_promotion_duplicate_conflict",
            f"blueprint_promotion_duplicate_conflict: promotion_id={promotion.promotion_id}",
        )
    return True


def _normalized_task_payload(task: TaskDocument) -> str:
    return json.dumps(
        task.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    )


def _ensure_contains_all(
    actual: tuple[str, ...],
    expected: tuple[str, ...],
    *,
    field_name: str,
) -> None:
    missing = [item for item in expected if item not in actual]
    if missing:
        raise ValueError(f"{field_name} missing Blueprint item(s): {', '.join(missing)}")


def _ensure_task_id_unused(paths: WorkspacePaths, task_id: str) -> None:
    filename = f"{task_id}.md"
    for directory in (
        paths.tasks_queue_dir,
        paths.tasks_active_dir,
        paths.tasks_done_dir,
        paths.tasks_blocked_dir,
    ):
        if (directory / filename).exists():
            raise QueueStateError(f"task {task_id} already exists")


def _ensure_promotion_id_unused(paths: WorkspacePaths, evaluation_id: str) -> None:
    path = paths.runtime_root / "blueprints" / "promotions" / f"{_promotion_id(evaluation_id)}.json"
    if path.exists():
        raise QueueStateError(f"Blueprint promotion already exists for evaluation {evaluation_id}")


def _move_candidate_markdown(
    paths: WorkspacePaths,
    blueprint_id: str,
    *,
    target_state: str,
) -> Path | None:
    source = paths.runtime_root / "blueprints" / "packets" / "candidates" / f"{blueprint_id}.md"
    if not source.exists():
        return None
    destination = paths.runtime_root / "blueprints" / "packets" / target_state / source.name
    if destination.exists():
        raise QueueStateError(f"blueprint markdown {blueprint_id} already exists at destination")
    destination.parent.mkdir(parents=True, exist_ok=True)
    source.replace(destination)
    return destination


def _task_with_blueprint_refs(
    task: TaskDocument,
    *,
    paths: WorkspacePaths,
    approved_blueprint_path: Path,
    evaluation_path: Path,
) -> TaskDocument:
    refs = _unique_tuple(
        (
            *task.references,
            _effect_path(paths, approved_blueprint_path),
            _effect_path(paths, evaluation_path),
        )
    )
    return task.model_copy(update={"references": refs})


def _source_lifecycle_intent(
    stage_result: StageResultEnvelope,
    *,
    plan_id: str,
    action: SourceLifecycleAction,
) -> SourceLifecycleIntent:
    return SourceLifecycleIntent(
        lifecycle_plan_id=plan_id,
        action=action,
        work_item_kind=stage_result.work_item_kind,
        work_item_id=stage_result.work_item_id,
    )


def _failure_result(
    handler_id: str,
    stage_result: StageResultEnvelope,
    *,
    failure_class: str,
    message: str,
    created_paths: Sequence[str],
) -> RuntimeEffectResult:
    return RuntimeEffectResult(
        handler_id=handler_id,
        decision=RuntimeEffectDecision.REQUEST_BLOCK_SOURCE,
        created_paths=tuple(created_paths),
        source_lifecycle_intent=_source_lifecycle_intent(
            stage_result,
            plan_id=_block_lifecycle_plan_id(stage_result.work_item_kind),
            action=SourceLifecycleAction.BLOCK,
        ),
        failure_class=failure_class,
        message=message,
        mutation_phase=(
            RuntimeEffectMutationPhase.PARTIAL_MUTATION
            if created_paths
            else RuntimeEffectMutationPhase.PRE_MUTATION
        ),
    )


def _approval_failure_class(exc: Exception, created_paths: Sequence[str]) -> str:
    if created_paths:
        return "blueprint_partial_mutation"
    if isinstance(exc, RuntimeArtifactError) and exc.artifact_id == "generated_task":
        if exc.failure_class == "artifact_missing":
            return "generated_task_missing"
        return "generated_task_invalid"
    if isinstance(exc, QueueStateError) and "task" in str(exc) and "already exists" in str(exc):
        return "blueprint_task_duplicate"
    if isinstance(exc, ValidationError):
        return "blueprint_task_promotion_invalid"
    message = str(exc)
    if "generated task" in message:
        return "generated_task_invalid"
    return "blueprint_evaluation_invalid"


def _complete_lifecycle_plan_id(work_item_kind: WorkItemKind) -> str:
    if work_item_kind is WorkItemKind.SPEC:
        return "complete_spec_source_after_blueprint_effect"
    if work_item_kind is WorkItemKind.INCIDENT:
        return "complete_incident_source_after_blueprint_effect"
    if work_item_kind is WorkItemKind.BLUEPRINT_DRAFT:
        return "approve_blueprint_draft_after_effect"
    return "complete_source_after_effect"


def _block_lifecycle_plan_id(work_item_kind: WorkItemKind) -> str:
    if work_item_kind is WorkItemKind.SPEC:
        return "block_spec_source_after_blueprint_effect"
    if work_item_kind is WorkItemKind.INCIDENT:
        return "block_incident_source_after_blueprint_effect"
    if work_item_kind is WorkItemKind.BLUEPRINT_DRAFT:
        return "block_blueprint_draft_after_effect"
    return "block_source_after_effect"


def _copy_unique_file(source: Path, destination: Path) -> None:
    if destination.exists():
        raise QueueStateError(f"Blueprint artifact already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = destination.with_name(f".{destination.name}.tmp")
    copyfile(source, tmp_path)
    tmp_path.replace(destination)


def _effect_path(paths: WorkspacePaths, path: Path) -> str:
    return path.relative_to(paths.root).as_posix()


def _promotion_id(evaluation_id: str) -> str:
    return f"promotion-{evaluation_id}"


def _unique_tuple(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


BlueprintModelT = (
    BlueprintManifestDocument
    | BlueprintDraftDocument
    | BlueprintPacketDocument
    | BlueprintEvaluationDocument
    | BlueprintCritiqueDocument
    | BlueprintPromotionRecord
    | BlueprintRepairDecisionDocument
)


__all__ = [
    "CONTRACTOR_BLUEPRINT_HANDLER_ID",
    "EVALUATOR_BLUEPRINT_APPROVAL_HANDLER_ID",
    "EVALUATOR_BLUEPRINT_REJECTION_HANDLER_ID",
    "MANAGER_BLUEPRINT_HANDLER_ID",
    "MECHANIC_BLUEPRINT_REPAIR_HANDLER_ID",
    "contractor_blueprint_candidate_persist",
    "evaluator_blueprint_approved_to_task",
    "evaluator_blueprint_rejected_to_draft_revision",
    "manager_blueprint_manifest_to_blueprint_drafts",
    "mechanic_blueprint_repair_apply",
]
