"""Manager Blueprint runtime-effect operation runner."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import JsonValue

from millrace_ai.contracts import StageResultEnvelope, WorkItemKind
from millrace_ai.contracts.blueprint import BlueprintDraftDocument, BlueprintManifestDocument
from millrace_ai.errors import QueueStateError
from millrace_ai.workspace.blueprint_state import (
    enqueue_blueprint_draft,
    read_blueprint_draft,
    read_blueprint_manifest,
    write_blueprint_manifest,
)
from millrace_ai.workspace.paths import WorkspacePaths

from ..models import RuntimeEffectDecision, RuntimeEffectMutationPhase, RuntimeEffectResult
from .artifact_workflow_common import (
    _effect_path,
    _normalized_blueprint_model_payload,
    _stage_result_work_item_kind,
)
from .artifacts import read_json_model_list_payload, read_json_model_payload
from .results import block_source_failure_result, complete_source_success_result, runtime_mutation_journal
from .types import ModelT

if TYPE_CHECKING:
    from millrace_ai.architecture import CompiledRunPlan

MANAGER_BLUEPRINT_OPERATION_ID = "manager_blueprint_manifest_to_blueprint_drafts"
_LATEST_PACKET_FIELD = "latest_" "blueprint_id"

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
            return _manager_success_result(
                created_paths=created_paths,
                complete_source=True,
                message=f"queued {len(drafts)} blueprint draft(s)",
                mutation_journal=mutation_journal,
            )
        if source_state == "target":
            return _manager_success_result(
                created_paths=created_paths,
                complete_source=False,
                message=f"blueprint draft output already exists for {manifest.manifest_id}",
            )
        return _manager_failure_result(
            stage_result,
            failure_class="blueprint_source_lifecycle_invalid",
            message=f"source work item is not active: {stage_result.work_item_id}",
            created_paths=created_paths,
        )

    if source_state != "active":
        return _manager_failure_result(
            stage_result,
            failure_class="blueprint_source_lifecycle_invalid",
            message=f"source work item is not active: {stage_result.work_item_id}",
            created_paths=created_paths,
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

    return _manager_success_result(
        created_paths=created_paths,
        complete_source=True,
        message=f"queued {len(drafts)} blueprint draft(s)",
        mutation_journal=mutation_journal,
    )


def _read_manager_json_model(
    path: Path,
    model: type[ModelT],
    *,
    missing_class: str,
    parse_class: str,
    schema_class: str,
) -> ModelT:
    return read_json_model_payload(
        path,
        model,
        missing_class=missing_class,
        parse_class=parse_class,
        schema_class=schema_class,
        error_factory=_ManagerBlueprintEffectError,
        missing_message=f"required Blueprint artifact is missing: {path.name}",
        read_error_message_prefix="required Blueprint artifact could not be read",
    )


def _read_manager_json_model_list(
    path: Path,
    model: type[ModelT],
    *,
    missing_class: str,
    parse_class: str,
    schema_class: str,
) -> tuple[ModelT, ...]:
    return read_json_model_list_payload(
        path,
        model,
        missing_class=missing_class,
        parse_class=parse_class,
        schema_class=schema_class,
        error_factory=_ManagerBlueprintEffectError,
        missing_message=f"required Blueprint artifact is missing: {path.name}",
        read_error_message_prefix="required Blueprint artifact could not be read",
    )


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
    complete_source: bool,
    message: str,
    mutation_journal: Sequence[dict[str, JsonValue]] = (),
) -> RuntimeEffectResult:
    if not complete_source:
        return RuntimeEffectResult(
            handler_id=MANAGER_BLUEPRINT_OPERATION_ID,
            decision=RuntimeEffectDecision.CONTINUE_ROUTE,
            created_paths=tuple(created_paths),
            message=message,
            mutation_journal=runtime_mutation_journal(mutation_journal),
        )
    return complete_source_success_result(
        MANAGER_BLUEPRINT_OPERATION_ID,
        created_paths=tuple(created_paths),
        source_lifecycle_intent=None,
        message=message,
        mutation_journal=mutation_journal,
    )


def _manager_failure_result(
    stage_result: StageResultEnvelope,
    *,
    failure_class: str,
    message: str,
    created_paths: Sequence[str],
    mutation_journal: Sequence[dict[str, JsonValue]] = (),
) -> RuntimeEffectResult:
    return block_source_failure_result(
        MANAGER_BLUEPRINT_OPERATION_ID,
        stage_result,
        failure_class=failure_class,
        message=message,
        created_paths=created_paths,
        mutation_journal=mutation_journal,
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


def _manager_draft_identity_payload(draft: BlueprintDraftDocument) -> str:
    return json.dumps(
        draft.model_dump(
            mode="json",
            exclude={
                "status",
                "current_revision",
                _LATEST_PACKET_FIELD,
                "latest_critique_id",
                "updated_at",
            },
        ),
        sort_keys=True,
        separators=(",", ":"),
    )

__all__ = [
    "MANAGER_BLUEPRINT_OPERATION_ID",
    "enqueue_blueprint_draft",
    "manager_blueprint_manifest_to_blueprint_drafts",
]
