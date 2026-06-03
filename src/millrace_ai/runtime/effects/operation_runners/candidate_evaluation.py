"""Evaluator Blueprint runtime-effect operation runners."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import JsonValue, ValidationError

from millrace_ai.contracts import StageResultEnvelope, WorkItemKind
from millrace_ai.contracts.blueprint import (
    BlueprintCritiqueDocument,
    BlueprintDraftDocument,
    BlueprintEvaluationDocument,
    BlueprintPacketDocument,
    BlueprintPromotionRecord,
)
from millrace_ai.contracts.work_documents import TaskDocument
from millrace_ai.errors import QueueStateError
from millrace_ai.workspace.blueprint_state import (
    blueprint_packet_path,
    move_candidate_blueprint_packet,
    persist_blueprint_critique,
    persist_blueprint_evaluation,
    persist_blueprint_promotion,
    read_blueprint_draft,
    read_blueprint_packet,
    update_active_blueprint_draft,
)
from millrace_ai.workspace.paths import WorkspacePaths
from millrace_ai.workspace.work_documents import read_work_document_as

from ...artifact_contracts import RuntimeArtifactError
from ..models import (
    RuntimeEffectDecision,
    RuntimeEffectMutationPhase,
    RuntimeEffectResult,
)
from .artifact_workflow_common import (
    _effect_path,
    _normalized_blueprint_model_payload,
    _normalized_markdown_content,
    _read_json_model,
    _runtime_mutation_journal,
    _stage_result_work_item_kind,
)
from .artifacts import parse_required_run_artifact_as
from .candidate_packet import (
    _candidate_markdown_path,
    _contractor_active_draft_for_stage_result,
)
from .idempotency import ensure_contains_all, normalized_markdown_sha256, read_markdown_checksum, unique_tuple
from .results import block_source_failure_result
from .work_items import enqueue_task_document as enqueue_task

if TYPE_CHECKING:
    from millrace_ai.architecture import CompiledRunPlan

EVALUATOR_BLUEPRINT_APPROVAL_OPERATION_ID = "evaluator_blueprint_approved_to_task"
EVALUATOR_BLUEPRINT_REJECTION_OPERATION_ID = "evaluator_blueprint_rejected_to_draft_revision"
_LATEST_PACKET_FIELD = "latest_" "blueprint_id"

@dataclass(frozen=True, slots=True)
class _ApprovalBlueprintEffectError(Exception):
    failure_class: str
    message: str

    def __str__(self) -> str:
        return self.message

def evaluator_blueprint_approved_to_task(
    paths: WorkspacePaths,
    stage_result: StageResultEnvelope,
    run_dir: Path,
    compiled_plan: CompiledRunPlan | None = None,
) -> RuntimeEffectResult:
    """Run the compiled Evaluator Blueprint approval operation."""

    created_paths: list[str] = []
    mutation_journal: list[dict[str, JsonValue]] = []
    try:
        draft, source_state = _approval_draft_for_stage_result(paths, stage_result)
        packet = _approval_packet_for_draft(paths, draft)
        evaluation = _read_json_model(
            run_dir / "blueprint_evaluation.json",
            BlueprintEvaluationDocument,
        )
        _validate_approval(evaluation, packet)
        task = parse_required_run_artifact_as(
            compiled_plan,
            "generated_task",
            run_dir,
            TaskDocument,
        )
        _validate_generated_task(task, draft, packet)
        return _promote_approved_blueprint_task(
            paths,
            operation_id=EVALUATOR_BLUEPRINT_APPROVAL_OPERATION_ID,
            stage_result=stage_result,
            draft=draft,
            source_state=source_state,
            packet=packet,
            evaluation=evaluation,
            task=task,
            run_dir=run_dir,
            created_paths=created_paths,
            mutation_journal=mutation_journal,
        )
    except _ApprovalBlueprintEffectError as exc:
        return _evaluator_failure_result(
            EVALUATOR_BLUEPRINT_APPROVAL_OPERATION_ID,
            stage_result,
            failure_class=exc.failure_class,
            message=str(exc),
            created_paths=created_paths,
            mutation_journal=mutation_journal,
        )
    except Exception as exc:
        failure_class = _approval_failure_class(exc, created_paths)
        return _evaluator_failure_result(
            EVALUATOR_BLUEPRINT_APPROVAL_OPERATION_ID,
            stage_result,
            failure_class=failure_class,
            message=str(exc),
            created_paths=created_paths,
            mutation_journal=mutation_journal,
        )


def evaluator_blueprint_rejected_to_draft_revision(
    paths: WorkspacePaths,
    stage_result: StageResultEnvelope,
    run_dir: Path,
    compiled_plan: CompiledRunPlan | None = None,
) -> RuntimeEffectResult:
    """Run the compiled Evaluator Blueprint rejection operation."""

    created_paths: list[str] = []
    mutation_journal: list[dict[str, JsonValue]] = []
    try:
        draft = _contractor_active_draft_for_stage_result(paths, stage_result)
        packet, packet_state = _rejection_packet_for_draft(paths, draft)
        evaluation = _read_json_model(
            run_dir / "blueprint_evaluation.json",
            BlueprintEvaluationDocument,
        )
        critique = parse_required_run_artifact_as(
            compiled_plan,
            "blueprint_critique",
            run_dir,
            BlueprintCritiqueDocument,
        )
        _validate_rejection(evaluation, critique, packet)

        evaluation_exists = _blueprint_evaluation_exists_equivalent(
            paths,
            evaluation,
            failure_class="blueprint_rejection_duplicate_conflict",
        )
        rejected_packet_exists = _rejected_packet_exists_equivalent(paths, packet)
        expected_rejected_markdown_content = _expected_rejected_markdown_content(
            paths,
            packet.blueprint_id,
        )
        rejected_markdown_exists = _rejected_markdown_exists_equivalent(
            paths,
            packet.blueprint_id,
            expected_content=expected_rejected_markdown_content,
        )
        critique_exists = _blueprint_critique_exists_equivalent(paths, critique)

        if not evaluation_exists:
            evaluation_path = persist_blueprint_evaluation(paths, evaluation)
            created_path = _effect_path(paths, evaluation_path)
            created_paths.append(created_path)
            mutation_journal.append(
                _evaluator_mutation_journal_entry(
                    EVALUATOR_BLUEPRINT_REJECTION_OPERATION_ID,
                    stage_result,
                    step_id="persist_evaluation",
                    created_path=created_path,
                    blueprint_id=packet.blueprint_id,
                    evaluation_id=evaluation.evaluation_id,
                )
            )
        if not rejected_packet_exists:
            if packet_state != "candidates":
                raise _ApprovalBlueprintEffectError(
                    "blueprint_rejection_duplicate_conflict",
                    f"rejected packet {packet.blueprint_id} is missing but candidate packet is not available",
                )
            rejected_packet_path = move_candidate_blueprint_packet(
                paths,
                packet.blueprint_id,
                target_state="rejected",
            )
            rejected_packet_created_path = _effect_path(paths, rejected_packet_path)
            created_paths.append(rejected_packet_created_path)
            mutation_journal.append(
                _evaluator_mutation_journal_entry(
                    EVALUATOR_BLUEPRINT_REJECTION_OPERATION_ID,
                    stage_result,
                    step_id="move_candidate_packet_to_rejected",
                    created_path=rejected_packet_created_path,
                    moved_from=f"millrace-agents/blueprints/packets/candidates/{packet.blueprint_id}.json",
                    blueprint_id=packet.blueprint_id,
                )
            )
        if not rejected_markdown_exists:
            rejected_markdown_path = _move_candidate_markdown(
                paths,
                packet.blueprint_id,
                target_state="rejected",
            )
            if rejected_markdown_path is not None:
                created_path = _effect_path(paths, rejected_markdown_path)
                created_paths.append(created_path)
                mutation_journal.append(
                    _evaluator_mutation_journal_entry(
                        EVALUATOR_BLUEPRINT_REJECTION_OPERATION_ID,
                        stage_result,
                        step_id="move_candidate_markdown_to_rejected",
                        created_path=created_path,
                        moved_from=f"millrace-agents/blueprints/packets/candidates/{packet.blueprint_id}.md",
                        blueprint_id=packet.blueprint_id,
                    )
                )
        rejected_markdown_path = paths.runtime_root / "blueprints" / "packets" / "rejected" / f"{packet.blueprint_id}.md"
        if rejected_markdown_path.exists():
            checksum_path = _persist_markdown_checksum(
                paths,
                blueprint_id=packet.blueprint_id,
                packet_state="rejected",
                markdown_path=rejected_markdown_path,
                failure_class="blueprint_rejection_duplicate_conflict",
            )
            if checksum_path is not None:
                created_path = _effect_path(paths, checksum_path)
                created_paths.append(created_path)
                mutation_journal.append(
                    _evaluator_mutation_journal_entry(
                        EVALUATOR_BLUEPRINT_REJECTION_OPERATION_ID,
                        stage_result,
                        step_id="record_rejected_markdown_checksum",
                        created_path=created_path,
                        blueprint_id=packet.blueprint_id,
                        evaluation_id=evaluation.evaluation_id,
                    )
                )
        if not critique_exists:
            critique_path = persist_blueprint_critique(paths, critique, critique_state="open")
            created_path = _effect_path(paths, critique_path)
            created_paths.append(created_path)
            mutation_journal.append(
                _evaluator_mutation_journal_entry(
                    EVALUATOR_BLUEPRINT_REJECTION_OPERATION_ID,
                    stage_result,
                    step_id="persist_critique",
                    created_path=created_path,
                    blueprint_id=packet.blueprint_id,
                    evaluation_id=evaluation.evaluation_id,
                )
            )

        updated = draft.model_copy(
            update={
                "current_revision": packet.revision,
                "latest_critique_id": critique.critique_id,
                _LATEST_PACKET_FIELD: packet.blueprint_id,
                "updated_at": evaluation.created_at,
            }
        )
        if _normalized_blueprint_model_payload(draft) != _normalized_blueprint_model_payload(updated):
            draft_path = update_active_blueprint_draft(paths, updated)
            mutation_journal.append(
                _evaluator_mutation_journal_entry(
                    EVALUATOR_BLUEPRINT_REJECTION_OPERATION_ID,
                    stage_result,
                    step_id="update_active_draft_for_revision",
                    updated_path=_effect_path(paths, draft_path),
                    work_item_id=draft.draft_id,
                    blueprint_id=packet.blueprint_id,
                    evaluation_id=evaluation.evaluation_id,
                )
            )

        return RuntimeEffectResult(
            handler_id=EVALUATOR_BLUEPRINT_REJECTION_OPERATION_ID,
            decision=RuntimeEffectDecision.CONTINUE_ROUTE,
            created_paths=tuple(created_paths),
            message=f"recorded rejection critique {critique.critique_id}",
            mutation_journal=_runtime_mutation_journal(mutation_journal),
        )
    except _ApprovalBlueprintEffectError as exc:
        return _evaluator_failure_result(
            EVALUATOR_BLUEPRINT_REJECTION_OPERATION_ID,
            stage_result,
            failure_class=(
                "blueprint_partial_mutation"
                if created_paths
                else exc.failure_class
            ),
            message=str(exc),
            created_paths=created_paths,
            mutation_journal=mutation_journal,
        )
    except Exception as exc:
        return _evaluator_failure_result(
            EVALUATOR_BLUEPRINT_REJECTION_OPERATION_ID,
            stage_result,
            failure_class=(
                "blueprint_partial_mutation"
                if created_paths
                else "blueprint_critique_invalid"
            ),
            message=str(exc),
            created_paths=created_paths,
            mutation_journal=mutation_journal,
        )


def _candidate_packet_for_draft(
    paths: WorkspacePaths,
    draft: BlueprintDraftDocument,
) -> BlueprintPacketDocument:
    latest_packet_id = getattr(draft, _LATEST_PACKET_FIELD)
    if latest_packet_id is None:
        raise QueueStateError("active draft has no latest candidate blueprint")
    path = (
        paths.runtime_root
        / "blueprints"
        / "packets"
        / "candidates"
        / f"{latest_packet_id}.json"
    )
    packet = _read_json_model(path, BlueprintPacketDocument)
    packet.ensure_matches_draft(draft)
    return packet


def _rejection_packet_for_draft(
    paths: WorkspacePaths,
    draft: BlueprintDraftDocument,
) -> tuple[BlueprintPacketDocument, str]:
    latest_packet_id = getattr(draft, _LATEST_PACKET_FIELD)
    if latest_packet_id is None:
        raise QueueStateError("active draft has no latest candidate blueprint")
    entries: list[tuple[str, BlueprintPacketDocument]] = []
    for state in ("candidates", "rejected"):
        path = blueprint_packet_path(
            paths,
            latest_packet_id,
            packet_state=state,
        )
        if not path.exists():
            continue
        try:
            packet = read_blueprint_packet(
                paths,
                latest_packet_id,
                packet_state=state,
            )
            if state == "candidates":
                packet.ensure_matches_draft(draft)
            else:
                _ensure_rejected_packet_matches_draft(packet, draft)
        except (OSError, ValueError, ValidationError) as exc:
            raise _ApprovalBlueprintEffectError(
                "blueprint_rejection_duplicate_conflict",
                f"existing {state} packet {latest_packet_id} cannot be validated: {exc}",
            ) from exc
        entries.append((state, packet))
    if not entries:
        raise QueueStateError(f"candidate or rejected blueprint packet {latest_packet_id} not found")
    expected_payload = _normalized_blueprint_model_payload(entries[0][1])
    if any(_normalized_blueprint_model_payload(packet) != expected_payload for _state, packet in entries[1:]):
        raise _ApprovalBlueprintEffectError(
            "blueprint_rejection_duplicate_conflict",
            f"blueprint_rejection_duplicate_conflict: blueprint_id={latest_packet_id}",
        )
    state, packet = entries[0]
    return packet, state


def _ensure_rejected_packet_matches_draft(
    packet: BlueprintPacketDocument,
    draft: BlueprintDraftDocument,
) -> None:
    if packet.draft_id != draft.draft_id:
        raise ValueError("draft_id mismatch")
    if packet.manifest_id != draft.manifest_id:
        raise ValueError("manifest_id mismatch")
    if packet.root_spec_id != draft.root_spec_id:
        raise ValueError("root_spec_id mismatch")
    if packet.root_idea_id != draft.root_idea_id:
        raise ValueError("root_idea_id mismatch")
    if packet.revision not in {draft.current_revision, draft.current_revision + 1}:
        raise ValueError("revision must equal draft current_revision or current_revision + 1")


def _approval_draft_for_stage_result(
    paths: WorkspacePaths,
    stage_result: StageResultEnvelope,
) -> tuple[BlueprintDraftDocument, str]:
    stage_result_kind = _stage_result_work_item_kind(stage_result)
    if stage_result_kind is not WorkItemKind.BLUEPRINT_DRAFT:
        raise QueueStateError(
            f"Blueprint handler requires blueprint_draft source, got {stage_result_kind.value}"
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
    latest_packet_id = getattr(draft, _LATEST_PACKET_FIELD)
    if latest_packet_id is None:
        raise QueueStateError("active draft has no latest candidate blueprint")
    entries: list[tuple[str, BlueprintPacketDocument]] = []
    for state in ("candidates", "approved"):
        path = blueprint_packet_path(
            paths,
            latest_packet_id,
            packet_state=state,
        )
        if not path.exists():
            continue
        try:
            packet = read_blueprint_packet(
                paths,
                latest_packet_id,
                packet_state=state,
            )
            packet.ensure_matches_draft(draft)
        except (OSError, ValueError, ValidationError) as exc:
            raise _ApprovalBlueprintEffectError(
                "blueprint_approved_packet_conflict",
                f"existing {state} packet {latest_packet_id} cannot be validated: {exc}",
            ) from exc
        entries.append((state, packet))
    if not entries:
        raise QueueStateError(f"candidate or approved blueprint packet {latest_packet_id} not found")
    expected_payload = _normalized_blueprint_model_payload(entries[0][1])
    if any(_normalized_blueprint_model_payload(packet) != expected_payload for _state, packet in entries[1:]):
        raise _ApprovalBlueprintEffectError(
            "blueprint_approved_packet_conflict",
            f"blueprint_approved_packet_conflict: blueprint_id={latest_packet_id}",
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
    *,
    failure_class: str = "blueprint_evaluation_duplicate_conflict",
) -> bool:
    path = _blueprint_evaluation_path(paths, evaluation.evaluation_id)
    if not path.exists():
        return False
    try:
        existing = _read_json_model(path, BlueprintEvaluationDocument)
    except (OSError, ValueError, ValidationError) as exc:
        raise _ApprovalBlueprintEffectError(
            failure_class,
            f"existing evaluation {evaluation.evaluation_id} cannot be validated: {exc}",
        ) from exc
    if _normalized_blueprint_model_payload(existing) != _normalized_blueprint_model_payload(evaluation):
        raise _ApprovalBlueprintEffectError(
            failure_class,
            f"{failure_class}: evaluation_id={evaluation.evaluation_id}",
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
        if not _normalized_markdown_content(destination_content):
            raise _ApprovalBlueprintEffectError(
                "blueprint_approved_markdown_conflict",
                f"approved markdown {blueprint_id} is empty",
            )
        _ensure_markdown_checksum_matches(
            paths,
            blueprint_id=blueprint_id,
            packet_state="approved",
            content=destination_content,
            failure_class="blueprint_approved_markdown_conflict",
        )
        return True
    if _normalized_markdown_content(destination_content) != _normalized_markdown_content(
        expected_content,
    ):
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


def _rejected_packet_exists_equivalent(
    paths: WorkspacePaths,
    packet: BlueprintPacketDocument,
) -> bool:
    path = blueprint_packet_path(paths, packet.blueprint_id, packet_state="rejected")
    if not path.exists():
        return False
    try:
        existing = read_blueprint_packet(paths, packet.blueprint_id, packet_state="rejected")
    except (OSError, ValueError, ValidationError) as exc:
        raise _ApprovalBlueprintEffectError(
            "blueprint_rejection_duplicate_conflict",
            f"existing rejected packet {packet.blueprint_id} cannot be validated: {exc}",
        ) from exc
    if _normalized_blueprint_model_payload(existing) != _normalized_blueprint_model_payload(packet):
        raise _ApprovalBlueprintEffectError(
            "blueprint_rejection_duplicate_conflict",
            f"blueprint_rejection_duplicate_conflict: blueprint_id={packet.blueprint_id}",
        )
    return True


def _expected_rejected_markdown_content(
    paths: WorkspacePaths,
    blueprint_id: str,
) -> str | None:
    source = _candidate_markdown_path(paths, blueprint_id)
    if not source.exists():
        return None
    try:
        return source.read_text(encoding="utf-8")
    except OSError as exc:
        raise _ApprovalBlueprintEffectError(
            "blueprint_rejection_duplicate_conflict",
            f"expected rejected markdown {blueprint_id} cannot be read: {exc}",
        ) from exc


def _rejected_markdown_exists_equivalent(
    paths: WorkspacePaths,
    blueprint_id: str,
    *,
    expected_content: str | None,
) -> bool:
    destination = paths.runtime_root / "blueprints" / "packets" / "rejected" / f"{blueprint_id}.md"
    if not destination.exists():
        return False
    try:
        destination_content = destination.read_text(encoding="utf-8")
    except OSError as exc:
        raise _ApprovalBlueprintEffectError(
            "blueprint_rejection_duplicate_conflict",
            f"existing rejected markdown {blueprint_id} cannot be validated: {exc}",
        ) from exc
    if expected_content is None:
        _ensure_markdown_checksum_matches(
            paths,
            blueprint_id=blueprint_id,
            packet_state="rejected",
            content=destination_content,
            failure_class="blueprint_rejection_duplicate_conflict",
        )
        return True
    if _normalized_markdown_content(destination_content) != _normalized_markdown_content(
        expected_content,
    ):
        raise _ApprovalBlueprintEffectError(
            "blueprint_rejection_duplicate_conflict",
            f"blueprint_rejection_duplicate_conflict: markdown blueprint_id={blueprint_id}",
        )
    return True


def _markdown_checksum_path(
    paths: WorkspacePaths,
    *,
    blueprint_id: str,
    packet_state: str,
) -> Path:
    return paths.runtime_root / "blueprints" / "packets" / packet_state / f"{blueprint_id}.md.sha256"


def _persist_markdown_checksum(
    paths: WorkspacePaths,
    *,
    blueprint_id: str,
    packet_state: str,
    markdown_path: Path,
    failure_class: str,
) -> Path | None:
    try:
        content = markdown_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise _ApprovalBlueprintEffectError(
            failure_class,
            f"markdown {blueprint_id} cannot be read for checksum: {exc}",
        ) from exc
    checksum_path = _markdown_checksum_path(
        paths,
        blueprint_id=blueprint_id,
        packet_state=packet_state,
    )
    expected = _normalized_markdown_sha256(content)
    if checksum_path.exists():
        actual = _read_markdown_checksum(checksum_path, failure_class=failure_class)
        if actual != expected:
            raise _ApprovalBlueprintEffectError(
                failure_class,
                f"markdown checksum conflict: blueprint_id={blueprint_id}",
            )
        return None
    checksum_path.parent.mkdir(parents=True, exist_ok=True)
    checksum_path.write_text(f"{expected}\n", encoding="utf-8")
    return checksum_path


def _ensure_markdown_checksum_matches(
    paths: WorkspacePaths,
    *,
    blueprint_id: str,
    packet_state: str,
    content: str,
    failure_class: str,
) -> None:
    checksum_path = _markdown_checksum_path(
        paths,
        blueprint_id=blueprint_id,
        packet_state=packet_state,
    )
    if not checksum_path.exists():
        raise _ApprovalBlueprintEffectError(
            failure_class,
            f"markdown checksum missing: blueprint_id={blueprint_id}",
        )
    actual = _read_markdown_checksum(checksum_path, failure_class=failure_class)
    expected = _normalized_markdown_sha256(content)
    if actual != expected:
        raise _ApprovalBlueprintEffectError(
            failure_class,
            f"markdown checksum conflict: blueprint_id={blueprint_id}",
        )


def _read_markdown_checksum(path: Path, *, failure_class: str) -> str:
    return read_markdown_checksum(
        path,
        failure_class=failure_class,
        error_factory=_ApprovalBlueprintEffectError,
    )


def _normalized_markdown_sha256(content: str) -> str:
    return normalized_markdown_sha256(content)


def _blueprint_critique_exists_equivalent(
    paths: WorkspacePaths,
    critique: BlueprintCritiqueDocument,
) -> bool:
    path = paths.runtime_root / "blueprints" / "critiques" / "open" / f"{critique.critique_id}.json"
    if not path.exists():
        return False
    try:
        existing = _read_json_model(path, BlueprintCritiqueDocument)
    except (OSError, ValueError, ValidationError) as exc:
        raise _ApprovalBlueprintEffectError(
            "blueprint_rejection_duplicate_conflict",
            f"existing critique {critique.critique_id} cannot be validated: {exc}",
        ) from exc
    if _normalized_blueprint_model_payload(existing) != _normalized_blueprint_model_payload(critique):
        raise _ApprovalBlueprintEffectError(
            "blueprint_rejection_duplicate_conflict",
            f"blueprint_rejection_duplicate_conflict: critique_id={critique.critique_id}",
        )
    return True


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
    operation_id: str,
    stage_result: StageResultEnvelope,
    draft: BlueprintDraftDocument,
    source_state: str,
    packet: BlueprintPacketDocument,
    evaluation: BlueprintEvaluationDocument,
    task: TaskDocument,
    run_dir: Path,
    created_paths: list[str],
    mutation_journal: list[dict[str, JsonValue]],
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

    evaluation_exists = _blueprint_evaluation_exists_equivalent(paths, evaluation)
    approved_packet_exists = _approved_packet_exists_equivalent(paths, packet)
    task_exists = _generated_task_exists_equivalent(paths, task)
    promotion_exists = _promotion_exists_equivalent(paths, promotion)

    if not evaluation_exists:
        evaluation_path = persist_blueprint_evaluation(paths, evaluation)
        created_path = _effect_path(paths, evaluation_path)
        created_paths.append(created_path)
        mutation_journal.append(
            _evaluator_mutation_journal_entry(
                operation_id,
                stage_result,
                step_id="persist_evaluation",
                created_path=created_path,
                blueprint_id=packet.blueprint_id,
                evaluation_id=evaluation.evaluation_id,
            )
        )
    if not approved_packet_exists:
        approved_packet_path = move_candidate_blueprint_packet(
            paths,
            packet.blueprint_id,
            target_state="approved",
        )
        created_path = _effect_path(paths, approved_packet_path)
        created_paths.append(created_path)
        mutation_journal.append(
            _evaluator_mutation_journal_entry(
                operation_id,
                stage_result,
                step_id="move_candidate_packet_to_approved",
                created_path=created_path,
                moved_from=f"millrace-agents/blueprints/packets/candidates/{packet.blueprint_id}.json",
                blueprint_id=packet.blueprint_id,
            )
        )
    if not approved_markdown_exists:
        approved_markdown_path = _move_candidate_markdown(
            paths,
            packet.blueprint_id,
            target_state="approved",
        )
        if approved_markdown_path is not None:
            created_path = _effect_path(paths, approved_markdown_path)
            created_paths.append(created_path)
            mutation_journal.append(
                _evaluator_mutation_journal_entry(
                    operation_id,
                    stage_result,
                    step_id="move_candidate_markdown_to_approved",
                    created_path=created_path,
                    moved_from=f"millrace-agents/blueprints/packets/candidates/{packet.blueprint_id}.md",
                    blueprint_id=packet.blueprint_id,
                )
            )
        if not _approved_markdown_exists_equivalent(
            paths,
            packet.blueprint_id,
            expected_content=expected_markdown_content,
        ):
            raise _ApprovalBlueprintEffectError(
                "blueprint_approved_markdown_conflict",
                f"approved markdown {packet.blueprint_id} cannot be verified or reconstructed",
            )
    approved_markdown_path = _approved_markdown_path(paths, packet.blueprint_id)
    checksum_path = _persist_markdown_checksum(
        paths,
        blueprint_id=packet.blueprint_id,
        packet_state="approved",
        markdown_path=approved_markdown_path,
        failure_class="blueprint_approved_markdown_conflict",
    )
    if checksum_path is not None:
        created_path = _effect_path(paths, checksum_path)
        created_paths.append(created_path)
        mutation_journal.append(
            _evaluator_mutation_journal_entry(
                operation_id,
                stage_result,
                step_id="record_approved_markdown_checksum",
                created_path=created_path,
                blueprint_id=packet.blueprint_id,
                evaluation_id=evaluation.evaluation_id,
            )
        )
    if not task_exists:
        task_path = enqueue_task(paths, task)
        created_path = _effect_path(paths, task_path)
        created_paths.append(created_path)
        mutation_journal.append(
            _evaluator_mutation_journal_entry(
                operation_id,
                stage_result,
                step_id="enqueue_generated_task",
                created_path=created_path,
                blueprint_id=packet.blueprint_id,
                evaluation_id=evaluation.evaluation_id,
                work_item_id=task.task_id,
            )
        )
    if not promotion_exists:
        promotion_path = persist_blueprint_promotion(paths, promotion)
        created_path = _effect_path(paths, promotion_path)
        created_paths.append(created_path)
        mutation_journal.append(
            _evaluator_mutation_journal_entry(
                operation_id,
                stage_result,
                step_id="persist_promotion",
                created_path=created_path,
                blueprint_id=packet.blueprint_id,
                evaluation_id=evaluation.evaluation_id,
                work_item_id=task.task_id,
            )
        )

    return RuntimeEffectResult(
        handler_id=operation_id,
        decision=(
            RuntimeEffectDecision.REQUEST_COMPLETE_SOURCE
            if source_state == "active"
            else RuntimeEffectDecision.CONTINUE_ROUTE
        ),
        created_paths=tuple(created_paths),
        message=f"promoted blueprint {packet.blueprint_id} to task {task.task_id}",
        mutation_journal=_runtime_mutation_journal(mutation_journal),
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
    ensure_contains_all(
        actual,
        expected,
        field_name=field_name,
        missing_label="Blueprint item(s)",
    )


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


def _evaluator_failure_result(
    operation_id: str,
    stage_result: StageResultEnvelope,
    *,
    failure_class: str,
    message: str,
    created_paths: Sequence[str],
    mutation_journal: Sequence[dict[str, JsonValue]] = (),
) -> RuntimeEffectResult:
    return block_source_failure_result(
        operation_id,
        stage_result,
        failure_class=failure_class,
        message=message,
        created_paths=created_paths,
        mutation_journal=mutation_journal,
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


def _evaluator_mutation_journal_entry(
    operation_id: str,
    stage_result: StageResultEnvelope,
    *,
    step_id: str,
    blueprint_id: str,
    created_path: str | None = None,
    updated_path: str | None = None,
    moved_from: str | None = None,
    evaluation_id: str | None = None,
    work_item_id: str | None = None,
    source_lifecycle_action: str | None = None,
    work_item_family_id: str | None = None,
    work_item_kind: str | None = None,
) -> dict[str, JsonValue]:
    entry: dict[str, JsonValue] = {
        "operation_id": operation_id,
        "rule_id": operation_id,
        "run_id": stage_result.run_id,
        "step_id": step_id,
        "mutation_phase": RuntimeEffectMutationPhase.PARTIAL_MUTATION.value,
        "blueprint_id": blueprint_id,
    }
    if created_path is not None:
        entry["created_path"] = created_path
    if updated_path is not None:
        entry["updated_path"] = updated_path
    if moved_from is not None:
        entry["moved_from"] = moved_from
    if evaluation_id is not None:
        entry["evaluation_id"] = evaluation_id
    if work_item_id is not None:
        entry["work_item_id"] = work_item_id
    if source_lifecycle_action is not None:
        entry["source_lifecycle_action"] = source_lifecycle_action
    if work_item_family_id is not None:
        entry["work_item_family_id"] = work_item_family_id
    if work_item_kind is not None:
        entry["work_item_kind"] = work_item_kind
    return entry


def _promotion_id(evaluation_id: str) -> str:
    return f"promotion-{evaluation_id}"


def _unique_tuple(values: tuple[str, ...]) -> tuple[str, ...]:
    return unique_tuple(values)

__all__ = [
    "EVALUATOR_BLUEPRINT_APPROVAL_OPERATION_ID",
    "EVALUATOR_BLUEPRINT_REJECTION_OPERATION_ID",
    "enqueue_task",
    "evaluator_blueprint_approved_to_task",
    "evaluator_blueprint_rejected_to_draft_revision",
    "persist_blueprint_critique",
]
