"""Contractor Blueprint runtime-effect operation runner."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import JsonValue, ValidationError

from millrace_ai.contracts import StageResultEnvelope, WorkItemKind
from millrace_ai.contracts.blueprint import BlueprintDraftDocument, BlueprintPacketDocument
from millrace_ai.errors import QueueStateError
from millrace_ai.workspace.blueprint_state import (
    blueprint_packet_path,
    persist_blueprint_packet,
    read_active_blueprint_draft,
    read_blueprint_packet,
    update_active_blueprint_draft,
)
from millrace_ai.workspace.paths import WorkspacePaths

from ..models import RuntimeEffectDecision, RuntimeEffectMutationPhase, RuntimeEffectResult
from .blueprint_common import (
    _block_lifecycle_plan_id,
    _copy_unique_file,
    _effect_path,
    _normalized_blueprint_model_payload,
    _normalized_markdown_content,
    _read_json_model,
    _runtime_mutation_journal,
    _stage_result_work_item_kind,
)
from .results import block_source_failure_result

if TYPE_CHECKING:
    from millrace_ai.architecture import CompiledRunPlan

CONTRACTOR_BLUEPRINT_OPERATION_ID = "contractor_blueprint_candidate_persist"

@dataclass(frozen=True, slots=True)
class _ContractorBlueprintEffectError(Exception):
    failure_class: str
    message: str

    def __str__(self) -> str:
        return self.message

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


def _contractor_failure_result(
    stage_result: StageResultEnvelope,
    *,
    failure_class: str,
    message: str,
    created_paths: Sequence[str],
    mutation_journal: Sequence[dict[str, JsonValue]] = (),
) -> RuntimeEffectResult:
    return block_source_failure_result(
        CONTRACTOR_BLUEPRINT_OPERATION_ID,
        stage_result,
        failure_class=failure_class,
        message=message,
        created_paths=created_paths,
        lifecycle_plan_id=_block_lifecycle_plan_id(_stage_result_work_item_kind(stage_result)),
        mutation_journal=mutation_journal,
        context="Blueprint runtime effect",
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
    "contractor_blueprint_candidate_persist",
]
