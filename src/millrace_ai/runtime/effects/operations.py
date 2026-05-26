"""Runtime effect operation runners used by the registry seam."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from shutil import copyfile
from typing import TYPE_CHECKING, TypeVar

from pydantic import BaseModel, JsonValue, ValidationError

from millrace_ai.contracts import StageResultEnvelope, WorkItemKind
from millrace_ai.contracts.blueprint import (
    BlueprintDraftDocument,
    BlueprintManifestDocument,
    BlueprintPacketDocument,
)
from millrace_ai.errors import QueueStateError
from millrace_ai.workspace.blueprint_state import (
    blueprint_packet_path,
    enqueue_blueprint_draft,
    persist_blueprint_packet,
    read_active_blueprint_draft,
    read_blueprint_draft,
    read_blueprint_manifest,
    read_blueprint_packet,
    update_active_blueprint_draft,
    write_blueprint_manifest,
)
from millrace_ai.workspace.paths import WorkspacePaths

from .models import (
    RuntimeEffectDecision,
    RuntimeEffectMutationPhase,
    RuntimeEffectResult,
    SourceLifecycleAction,
    SourceLifecycleIntent,
)

if TYPE_CHECKING:
    from millrace_ai.architecture import CompiledRunPlan

MANAGER_BLUEPRINT_OPERATION_ID = "manager_blueprint_manifest_to_blueprint_drafts"
CONTRACTOR_BLUEPRINT_OPERATION_ID = "contractor_blueprint_candidate_persist"

BlueprintModelT = TypeVar("BlueprintModelT", bound=BaseModel)

_DRAFT_STATES: tuple[str, ...] = (
    "queue",
    "active",
    "approved",
    "blocked",
    "canceled",
    "superseded",
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


def manager_blueprint_manifest_to_blueprint_drafts(
    paths: WorkspacePaths,
    stage_result: StageResultEnvelope,
    run_dir: Path,
    compiled_plan: CompiledRunPlan | None = None,
) -> RuntimeEffectResult:
    """Run the compiled Manager Blueprint manifest-to-drafts operation."""

    del compiled_plan
    created_paths: list[str] = []
    mutation_journal: list[dict[str, JsonValue]] = []
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
            lifecycle_intent = _source_lifecycle_intent(
                stage_result,
                plan_id=_complete_lifecycle_plan_id(
                    _stage_result_work_item_kind(stage_result),
                ),
                action=SourceLifecycleAction.COMPLETE,
            )
            return _manager_success_result(
                created_paths=created_paths,
                source_lifecycle_intent=lifecycle_intent,
                message=f"queued {len(drafts)} blueprint draft(s)",
                mutation_journal=_append_lifecycle_journal(
                    mutation_journal,
                    _manager_lifecycle_journal_entry(stage_result, lifecycle_intent),
                ),
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
            created_path = _effect_path(paths, manifest_path)
            created_paths.append(created_path)
            mutation_journal.append(
                _manager_mutation_journal_entry(
                    stage_result,
                    step_id="persist_manifest",
                    created_path=created_path,
                )
            )
        for draft in drafts:
            if draft.draft_id in existing_draft_ids:
                continue
            draft_path = enqueue_blueprint_draft(paths, draft)
            created_path = _effect_path(paths, draft_path)
            created_paths.append(created_path)
            mutation_journal.append(
                _manager_mutation_journal_entry(
                    stage_result,
                    step_id="enqueue_drafts",
                    created_path=created_path,
                    work_item_id=draft.draft_id,
                )
            )
    except (OSError, QueueStateError) as exc:
        return _manager_failure_result(
            stage_result,
            failure_class=_manager_write_failure_class(exc, created_paths),
            message=str(exc),
            created_paths=created_paths,
            mutation_journal=mutation_journal,
        )

    lifecycle_intent = _source_lifecycle_intent(
        stage_result,
        plan_id=_complete_lifecycle_plan_id(_stage_result_work_item_kind(stage_result)),
        action=SourceLifecycleAction.COMPLETE,
    )
    return _manager_success_result(
        created_paths=created_paths,
        source_lifecycle_intent=lifecycle_intent,
        message=f"queued {len(drafts)} blueprint draft(s)",
        mutation_journal=_append_lifecycle_journal(
            mutation_journal,
            _manager_lifecycle_journal_entry(stage_result, lifecycle_intent),
        ),
    )


def contractor_blueprint_candidate_persist(
    paths: WorkspacePaths,
    stage_result: StageResultEnvelope,
    run_dir: Path,
    compiled_plan: CompiledRunPlan | None = None,
) -> RuntimeEffectResult:
    """Run the compiled Contractor Blueprint candidate persistence operation."""

    del compiled_plan
    created_paths: list[str] = []
    mutation_journal: list[dict[str, JsonValue]] = []
    try:
        draft = _contractor_active_draft_for_stage_result(paths, stage_result)
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
            created_path = _effect_path(paths, packet_path)
            created_paths.append(created_path)
            mutation_journal.append(
                _contractor_mutation_journal_entry(
                    stage_result,
                    step_id="persist_candidate_packet",
                    created_path=created_path,
                    blueprint_id=packet.blueprint_id,
                )
            )
        if not markdown_exists:
            markdown_path = _persist_candidate_markdown(paths, packet.blueprint_id, run_dir)
            created_path = _effect_path(paths, markdown_path)
            created_paths.append(created_path)
            mutation_journal.append(
                _contractor_mutation_journal_entry(
                    stage_result,
                    step_id="copy_candidate_markdown",
                    created_path=created_path,
                    blueprint_id=packet.blueprint_id,
                )
            )

        updated = draft.model_copy(update={"latest_blueprint_id": packet.blueprint_id})
        draft_path = update_active_blueprint_draft(paths, updated)
        mutation_journal.append(
            _contractor_mutation_journal_entry(
                stage_result,
                step_id="update_active_draft",
                updated_path=_effect_path(paths, draft_path),
                work_item_id=draft.draft_id,
                blueprint_id=packet.blueprint_id,
            )
        )

        return RuntimeEffectResult(
            handler_id=CONTRACTOR_BLUEPRINT_OPERATION_ID,
            decision=RuntimeEffectDecision.CONTINUE_ROUTE,
            created_paths=tuple(created_paths),
            message=(
                f"persisted candidate blueprint {packet.blueprint_id}"
                if created_paths
                else f"candidate blueprint {packet.blueprint_id} already persisted"
            ),
            mutation_journal=_runtime_mutation_journal(mutation_journal),
        )
    except _ContractorBlueprintEffectError as exc:
        return _contractor_failure_result(
            stage_result,
            failure_class=exc.failure_class,
            message=str(exc),
            created_paths=created_paths,
            mutation_journal=mutation_journal,
        )
    except Exception as exc:
        return _contractor_failure_result(
            stage_result,
            failure_class=(
                "blueprint_partial_mutation"
                if created_paths
                else "blueprint_candidate_invalid"
            ),
            message=str(exc),
            created_paths=created_paths,
            mutation_journal=mutation_journal,
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
    for state in _DRAFT_STATES:
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
    mutation_journal: Sequence[dict[str, JsonValue]] = (),
) -> RuntimeEffectResult:
    return RuntimeEffectResult(
        handler_id=MANAGER_BLUEPRINT_OPERATION_ID,
        decision=RuntimeEffectDecision.REQUEST_COMPLETE_SOURCE,
        created_paths=tuple(created_paths),
        source_lifecycle_intent=source_lifecycle_intent,
        message=message,
        mutation_journal=_runtime_mutation_journal(mutation_journal),
    )


def _manager_failure_result(
    stage_result: StageResultEnvelope,
    *,
    failure_class: str,
    message: str,
    created_paths: Sequence[str],
    include_source_lifecycle_intent: bool = True,
    mutation_journal: Sequence[dict[str, JsonValue]] = (),
) -> RuntimeEffectResult:
    return RuntimeEffectResult(
        handler_id=MANAGER_BLUEPRINT_OPERATION_ID,
        decision=RuntimeEffectDecision.REQUEST_BLOCK_SOURCE,
        created_paths=tuple(created_paths),
        source_lifecycle_intent=(
            _source_lifecycle_intent(
                stage_result,
                plan_id=_block_lifecycle_plan_id(_stage_result_work_item_kind(stage_result)),
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
        mutation_journal=_runtime_mutation_journal(mutation_journal),
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


def _manager_mutation_journal_entry(
    stage_result: StageResultEnvelope,
    *,
    step_id: str,
    created_path: str | None = None,
    work_item_id: str | None = None,
) -> dict[str, JsonValue]:
    entry: dict[str, JsonValue] = {
        "operation_id": MANAGER_BLUEPRINT_OPERATION_ID,
        "rule_id": MANAGER_BLUEPRINT_OPERATION_ID,
        "run_id": stage_result.run_id,
        "step_id": step_id,
        "mutation_phase": RuntimeEffectMutationPhase.PARTIAL_MUTATION.value,
    }
    if created_path is not None:
        entry["created_path"] = created_path
    if work_item_id is not None:
        entry["work_item_id"] = work_item_id
    return entry


def _manager_lifecycle_journal_entry(
    stage_result: StageResultEnvelope,
    intent: SourceLifecycleIntent | None,
) -> dict[str, JsonValue] | None:
    if intent is None:
        return None
    return {
        "operation_id": MANAGER_BLUEPRINT_OPERATION_ID,
        "rule_id": MANAGER_BLUEPRINT_OPERATION_ID,
        "run_id": stage_result.run_id,
        "step_id": "complete_source_lifecycle",
        "mutation_phase": RuntimeEffectMutationPhase.PARTIAL_MUTATION.value,
        "source_lifecycle_action": intent.action.value,
        "work_item_family_id": intent.work_item_family_id,
        "work_item_kind": (
            intent.work_item_kind.value if intent.work_item_kind is not None else None
        ),
        "work_item_id": intent.work_item_id,
    }


def _append_lifecycle_journal(
    mutation_journal: Sequence[dict[str, JsonValue]],
    lifecycle_entry: dict[str, JsonValue] | None,
) -> tuple[dict[str, JsonValue], ...]:
    entries = list(mutation_journal)
    if lifecycle_entry is not None:
        entries.append(lifecycle_entry)
    return _runtime_mutation_journal(entries)


def _runtime_mutation_journal(
    entries: Sequence[dict[str, JsonValue]],
) -> tuple[dict[str, JsonValue], ...]:
    return tuple(dict(entry) for entry in entries)


def _validate_manager_output(
    stage_result: StageResultEnvelope,
    manifest: BlueprintManifestDocument,
    drafts: Sequence[BlueprintDraftDocument],
) -> None:
    if manifest.source_work_item_kind != _stage_result_work_item_kind(stage_result).value:
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


def _source_lifecycle_intent(
    stage_result: StageResultEnvelope,
    *,
    plan_id: str,
    action: SourceLifecycleAction,
) -> SourceLifecycleIntent:
    return SourceLifecycleIntent(
        lifecycle_plan_id=plan_id,
        action=action,
        work_item_kind=_stage_result_work_item_kind(stage_result),
        work_item_id=stage_result.work_item_id,
    )


def _stage_result_work_item_kind(stage_result: StageResultEnvelope) -> WorkItemKind:
    if stage_result.work_item_kind is None:
        raise QueueStateError("Blueprint runtime effect requires stage result work_item_kind")
    return stage_result.work_item_kind


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


def _effect_path(paths: WorkspacePaths, path: Path) -> str:
    return path.relative_to(paths.root).as_posix()


def _normalized_blueprint_model_payload(document: BaseModel) -> str:
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


def _contractor_active_draft_for_stage_result(
    paths: WorkspacePaths,
    stage_result: StageResultEnvelope,
) -> BlueprintDraftDocument:
    stage_result_kind = _stage_result_work_item_kind(stage_result)
    if stage_result_kind is not WorkItemKind.BLUEPRINT_DRAFT:
        raise QueueStateError(
            f"Blueprint handler requires blueprint_draft source, got {stage_result_kind.value}"
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


def _copy_unique_file(source: Path, destination: Path) -> None:
    if destination.exists():
        raise QueueStateError(f"Blueprint artifact already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = destination.with_name(f".{destination.name}.tmp")
    copyfile(source, tmp_path)
    tmp_path.replace(destination)


def _read_json_model(path: Path, model: type[BlueprintModelT]) -> BlueprintModelT:
    if not path.exists():
        raise QueueStateError(f"required Blueprint artifact is missing: {path.name}")
    return model.model_validate_json(path.read_text(encoding="utf-8"))


def _contractor_failure_result(
    stage_result: StageResultEnvelope,
    *,
    failure_class: str,
    message: str,
    created_paths: Sequence[str],
    mutation_journal: Sequence[dict[str, JsonValue]] = (),
) -> RuntimeEffectResult:
    return RuntimeEffectResult(
        handler_id=CONTRACTOR_BLUEPRINT_OPERATION_ID,
        decision=RuntimeEffectDecision.REQUEST_BLOCK_SOURCE,
        created_paths=tuple(created_paths),
        source_lifecycle_intent=_source_lifecycle_intent(
            stage_result,
            plan_id=_block_lifecycle_plan_id(_stage_result_work_item_kind(stage_result)),
            action=SourceLifecycleAction.BLOCK,
        ),
        failure_class=failure_class,
        message=message,
        mutation_phase=(
            RuntimeEffectMutationPhase.PARTIAL_MUTATION
            if created_paths
            else RuntimeEffectMutationPhase.PRE_MUTATION
        ),
        mutation_journal=_runtime_mutation_journal(mutation_journal),
    )


def _contractor_mutation_journal_entry(
    stage_result: StageResultEnvelope,
    *,
    step_id: str,
    blueprint_id: str,
    created_path: str | None = None,
    updated_path: str | None = None,
    work_item_id: str | None = None,
) -> dict[str, JsonValue]:
    entry: dict[str, JsonValue] = {
        "operation_id": CONTRACTOR_BLUEPRINT_OPERATION_ID,
        "rule_id": CONTRACTOR_BLUEPRINT_OPERATION_ID,
        "run_id": stage_result.run_id,
        "step_id": step_id,
        "mutation_phase": RuntimeEffectMutationPhase.PARTIAL_MUTATION.value,
        "blueprint_id": blueprint_id,
    }
    if created_path is not None:
        entry["created_path"] = created_path
    if updated_path is not None:
        entry["updated_path"] = updated_path
    if work_item_id is not None:
        entry["work_item_id"] = work_item_id
    return entry


__all__ = [
    "CONTRACTOR_BLUEPRINT_OPERATION_ID",
    "MANAGER_BLUEPRINT_OPERATION_ID",
    "contractor_blueprint_candidate_persist",
    "manager_blueprint_manifest_to_blueprint_drafts",
]
