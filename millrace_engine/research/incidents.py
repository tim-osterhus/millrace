"""Typed incident queue contracts plus executable incident stage helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from ..contracts import ContractModel
from ..paths import RuntimePaths
from .incident_document_rendering import _slugify
from .incident_documents import (
    IncidentArchiveRecord,
    IncidentDocument,
    IncidentFixSpecRecord,
    IncidentIntakeRecord,
    IncidentLifecycleStatus,
    IncidentLineageRecord,
    IncidentRecurrenceLedger,
    IncidentRecurrenceObservation,
    IncidentRecurrenceRecord,
    IncidentRemediationRecord,
    IncidentResolveRecord,
    IncidentSeverity,
    _INCIDENT_ARTIFACT_SCHEMA_VERSION,
    load_incident_document,
    parse_incident_document,
)
from .incident_intake_helpers import (
    incident_source_exists,
    incident_source_on_disk,
    materialize_incident_source,
    move_incident as _move_incident,
    queue_ownership_for_incident_path as _queue_ownership_for_incident_path,
    resolve_incident_source,
    target_incident_path as _target_incident_path,
)
from .incident_remediation_helpers import (
    load_incident_remediation_record as _load_incident_remediation_record,
    write_incident_remediation_bundle as _write_incident_remediation_bundle,
)
from .incident_state_helpers import (
    incident_archive_evidence_paths as _incident_archive_evidence_paths,
    lineages_context as _lineage_context,
    persist_recovery_decision as _persist_recovery_decision,
    updated_lineage_record as _updated_lineage_record,
)
from .normalization_helpers import _normalize_optional_text_or_none, _normalize_required_text
from .path_helpers import _relative_path, _resolve_path_token
from .persistence_helpers import _load_json_object, _sha256_text, _write_json_model as _shared_write_json_model

if TYPE_CHECKING:
    from .dispatcher import CompiledResearchDispatch
    from .state import ResearchCheckpoint, ResearchQueueOwnership


def _write_json_model(path: Path, model: ContractModel) -> None:
    _shared_write_json_model(path, model, create_parent=True)


class IncidentExecutionError(RuntimeError):
    """Raised when one incident stage cannot execute safely."""


@dataclass(frozen=True, slots=True)
class IncidentIntakeExecutionResult:
    """Resolved outputs from one incident intake execution."""

    record_path: str
    lineage_path: str
    working_path: str
    queue_ownership: ResearchQueueOwnership


@dataclass(frozen=True, slots=True)
class IncidentResolveExecutionResult:
    """Resolved outputs from one incident resolve execution."""

    record_path: str
    lineage_path: str
    resolved_path: str
    remediation_record_path: str
    reviewed_spec_path: str
    queue_ownership: ResearchQueueOwnership


@dataclass(frozen=True, slots=True)
class IncidentArchiveExecutionResult:
    """Resolved outputs from one incident archive execution."""

    record_path: str
    lineage_path: str
    archived_path: str
    queue_ownership: ResearchQueueOwnership


@dataclass(frozen=True, slots=True)
class IncidentTaskGenerationExecutionResult:
    """Resolved outputs from one incident remediation task-generation handoff."""

    remediation_record_path: str
    taskmaster_record_path: str
    taskaudit_record_path: str


def _incident_runtime_dir(paths: RuntimePaths) -> Path:
    return paths.research_runtime_dir / "incidents"


def _incident_lineage_path(paths: RuntimePaths, *, incident_key: str) -> Path:
    return _incident_runtime_dir(paths) / "lineage" / f"{incident_key}.json"


def _incident_record_path(paths: RuntimePaths, *, stage: str, run_id: str) -> Path:
    return _incident_runtime_dir(paths) / stage / f"{run_id}.json"


def _incident_remediation_record_path(paths: RuntimePaths, *, run_id: str) -> Path:
    return _incident_runtime_dir(paths) / "remediation" / f"{run_id}.json"


def default_incident_recurrence_ledger(*, observed_at: datetime | None = None) -> IncidentRecurrenceLedger:
    """Return an empty recurrence ledger snapshot."""

    return IncidentRecurrenceLedger(updated_at=observed_at or datetime.now(timezone.utc))


def incident_dedup_signature(
    fingerprint: str | None,
    failure_signature: str | None,
) -> str | None:
    """Return the canonical dedup signature for one incident recurrence pair."""

    normalized_fingerprint = _normalize_optional_text_or_none(fingerprint)
    normalized_failure_signature = _normalize_optional_text_or_none(failure_signature)
    if normalized_fingerprint is None or normalized_failure_signature is None:
        return None
    return _sha256_text(f"{normalized_fingerprint}|{normalized_failure_signature}")


def load_incident_recurrence_ledger(path: Path) -> IncidentRecurrenceLedger:
    """Load the incident recurrence ledger if present, else return the empty default."""

    if not path.exists():
        return default_incident_recurrence_ledger()
    try:
        payload = _load_json_object(path)
    except ValueError as exc:
        raise IncidentExecutionError(f"{path.as_posix()} must contain a JSON object") from exc
    return IncidentRecurrenceLedger.model_validate(payload)


def write_incident_recurrence_ledger(path: Path, ledger: IncidentRecurrenceLedger) -> None:
    """Persist the incident recurrence ledger."""

    _write_json_model(path, ledger)


def find_equivalent_incident(
    paths: RuntimePaths,
    *,
    fingerprint: str | None,
    failure_signature: str | None,
) -> Path | None:
    """Return the active incident path for one deduplicated fingerprint/signature pair."""

    dedup_signature = incident_dedup_signature(fingerprint, failure_signature)
    if dedup_signature is None:
        return None
    for queue_path in (
        paths.agents_dir / "ideas" / "incidents" / "incoming",
        paths.agents_dir / "ideas" / "incidents" / "working",
        paths.agents_dir / "ideas" / "incidents" / "resolved",
    ):
        if not queue_path.is_dir():
            continue
        for incident_path in sorted(path for path in queue_path.glob("*.md") if path.is_file()):
            document = load_incident_document(incident_path)
            if incident_dedup_signature(document.fingerprint, document.failure_signature) == dedup_signature:
                return incident_path
    return None


def resolve_deduplicated_incident_path(
    paths: RuntimePaths,
    *,
    fingerprint: str | None,
    failure_signature: str | None,
    preferred_path: Path | str | None = None,
) -> Path | None:
    """Return the canonical relative incident path for one recurring equivalent incident."""

    existing_path = find_equivalent_incident(
        paths,
        fingerprint=fingerprint,
        failure_signature=failure_signature,
    )
    if existing_path is not None:
        return Path(_relative_path(existing_path, relative_to=paths.root))

    if preferred_path is not None:
        resolved = _resolve_path_token(preferred_path, relative_to=paths.root)
        return Path(_relative_path(resolved, relative_to=paths.root))

    dedup_signature = incident_dedup_signature(fingerprint, failure_signature)
    if dedup_signature is not None:
        return Path("agents/ideas/incidents/incoming") / f"INC-{dedup_signature[:12].upper()}.md"

    return None


def record_incident_recurrence(
    paths: RuntimePaths,
    *,
    fingerprint: str | None,
    failure_signature: str | None,
    observed_at: datetime | None = None,
    source: Literal["execution_quarantine", "incident_intake", "incident_resolve", "incident_archive"],
    incident_id: str | None = None,
    incident_path: Path | str | None = None,
    lifecycle_status: str | None = None,
    source_task: str | None = None,
) -> IncidentRecurrenceRecord | None:
    """Append one recurrence observation for an equivalent incident pair."""

    dedup_signature = incident_dedup_signature(fingerprint, failure_signature)
    if dedup_signature is None:
        return None

    observed_at = observed_at or datetime.now(timezone.utc)
    normalized_fingerprint = _normalize_required_text(fingerprint or "", field_name="fingerprint")
    normalized_failure_signature = _normalize_required_text(failure_signature or "", field_name="failure_signature")
    relative_incident_path = None
    if incident_path is not None:
        relative_incident_path = _relative_path(
            _resolve_path_token(incident_path, relative_to=paths.root),
            relative_to=paths.root,
        )

    ledger = load_incident_recurrence_ledger(paths.incident_recurrence_ledger_file)
    observation = IncidentRecurrenceObservation(
        observed_at=observed_at,
        source=source,
        incident_id=incident_id,
        incident_path=relative_incident_path,
        lifecycle_status=lifecycle_status,
        source_task=source_task,
    )

    updated_record: IncidentRecurrenceRecord | None = None
    records: list[IncidentRecurrenceRecord] = []
    for record in ledger.records:
        if record.dedup_signature != dedup_signature:
            records.append(record)
            continue
        updated_record = record.model_copy(
            update={
                "last_seen_at": observed_at,
                "occurrence_count": record.occurrence_count + 1,
                "incident_id": incident_id or record.incident_id,
                "active_incident_path": relative_incident_path or record.active_incident_path,
                "source_task": source_task or record.source_task,
                "observations": record.observations + (observation,),
            }
        )
        records.append(updated_record)

    if updated_record is None:
        updated_record = IncidentRecurrenceRecord(
            dedup_signature=dedup_signature,
            fingerprint=normalized_fingerprint,
            failure_signature=normalized_failure_signature,
            first_seen_at=observed_at,
            last_seen_at=observed_at,
            occurrence_count=1,
            incident_id=incident_id,
            active_incident_path=relative_incident_path,
            source_task=source_task,
            observations=(observation,),
        )
        records.append(updated_record)

    write_incident_recurrence_ledger(
        paths.incident_recurrence_ledger_file,
        IncidentRecurrenceLedger(
            updated_at=observed_at,
            records=tuple(sorted(records, key=lambda item: item.dedup_signature)),
        ),
    )
    return updated_record


def _incident_key(document: IncidentDocument, source_path: Path) -> str:
    token = document.incident_id or source_path.stem
    return _slugify(token)



def execute_incident_intake(
    paths: RuntimePaths,
    checkpoint: ResearchCheckpoint,
    *,
    run_id: str,
    emitted_at: datetime | None = None,
) -> IncidentIntakeExecutionResult:
    """Move one incident into the working queue and persist intake lineage."""

    emitted_at = emitted_at or datetime.now(timezone.utc)
    source_path, document = resolve_incident_source(paths, checkpoint)
    current_path = source_path
    lifecycle = document.lifecycle_status or IncidentLifecycleStatus(source_path.parent.name)
    if lifecycle is IncidentLifecycleStatus.INCOMING:
        current_path = _move_incident(source_path, _target_incident_path(paths, lifecycle, source_path))
    lineage_path, lineage_record = _updated_lineage_record(
        paths=paths,
        checkpoint=checkpoint,
        document=document,
        source_path=source_path,
        current_path=current_path,
        stage="incident_intake",
        updated_at=emitted_at,
    )
    record_path = _incident_record_path(paths, stage="intake", run_id=run_id)
    source_task, blocker_ledger_path, blocker_item_key, parent_handoff_id, parent_run_id = _lineage_context(checkpoint)
    _write_json_model(
        record_path,
        IncidentIntakeRecord(
            run_id=run_id,
            emitted_at=emitted_at,
            incident_id=lineage_record.incident_id,
            title=document.title,
            source_path=_relative_path(source_path, relative_to=paths.root),
            working_path=_relative_path(current_path, relative_to=paths.root),
            lineage_path=_relative_path(lineage_path, relative_to=paths.root),
            source_task=source_task,
            blocker_ledger_path=blocker_ledger_path,
            blocker_item_key=blocker_item_key,
            parent_handoff_id=parent_handoff_id,
            parent_run_id=parent_run_id,
        ),
    )
    return IncidentIntakeExecutionResult(
        record_path=_relative_path(record_path, relative_to=paths.root),
        lineage_path=_relative_path(lineage_path, relative_to=paths.root),
        working_path=_relative_path(current_path, relative_to=paths.root),
        queue_ownership=_queue_ownership_for_incident_path(incident_path=current_path, run_id=run_id, emitted_at=emitted_at),
    )


def execute_incident_resolve(
    paths: RuntimePaths,
    checkpoint: ResearchCheckpoint,
    *,
    run_id: str,
    emitted_at: datetime | None = None,
) -> IncidentResolveExecutionResult:
    """Move one incident into the resolved queue and persist remediation evidence."""

    emitted_at = emitted_at or datetime.now(timezone.utc)
    source_path, document = resolve_incident_source(paths, checkpoint)
    current_path = source_path
    lifecycle = document.lifecycle_status or IncidentLifecycleStatus(source_path.parent.name)
    if lifecycle in {IncidentLifecycleStatus.INCOMING, IncidentLifecycleStatus.WORKING}:
        current_path = _move_incident(source_path, paths.agents_dir / "ideas" / "incidents" / "resolved" / source_path.name)
    lineage_path, lineage_record = _updated_lineage_record(
        paths=paths,
        checkpoint=checkpoint,
        document=document,
        source_path=source_path,
        current_path=current_path,
        stage="incident_resolve",
        updated_at=emitted_at,
    )
    remediation_record = _write_incident_remediation_bundle(
        paths,
        document=document,
        incident_path=current_path,
        lineage_path=lineage_path,
        run_id=run_id,
        emitted_at=emitted_at,
    )
    _write_json_model(
        lineage_path,
        lineage_record.model_copy(
            update={
                "remediation_spec_id": remediation_record.fix_spec.spec_id,
                "remediation_record_path": _relative_path(
                    _incident_remediation_record_path(paths, run_id=run_id),
                    relative_to=paths.root,
                ),
                "updated_at": emitted_at,
            }
        ),
    )
    record_path = _incident_record_path(paths, stage="resolve", run_id=run_id)
    source_task, blocker_ledger_path, blocker_item_key, parent_handoff_id, parent_run_id = _lineage_context(checkpoint)
    _write_json_model(
        record_path,
        IncidentResolveRecord(
            run_id=run_id,
            emitted_at=emitted_at,
            incident_id=lineage_record.incident_id,
            title=document.title,
            source_path=_relative_path(source_path, relative_to=paths.root),
            resolved_path=_relative_path(current_path, relative_to=paths.root),
            lineage_path=_relative_path(lineage_path, relative_to=paths.root),
            resolution_summary=f"Incident {lineage_record.incident_id} advanced to resolved remediation state.",
            source_task=source_task,
            blocker_ledger_path=blocker_ledger_path,
            blocker_item_key=blocker_item_key,
            parent_handoff_id=parent_handoff_id,
            parent_run_id=parent_run_id,
            remediation_record_path=_relative_path(
                _incident_remediation_record_path(paths, run_id=run_id),
                relative_to=paths.root,
            ),
            remediation_spec_id=remediation_record.fix_spec.spec_id,
        ),
    )
    return IncidentResolveExecutionResult(
        record_path=_relative_path(record_path, relative_to=paths.root),
        lineage_path=_relative_path(lineage_path, relative_to=paths.root),
        resolved_path=_relative_path(current_path, relative_to=paths.root),
        remediation_record_path=_relative_path(
            _incident_remediation_record_path(paths, run_id=run_id),
            relative_to=paths.root,
        ),
        reviewed_spec_path=remediation_record.fix_spec.reviewed_path,
        queue_ownership=_queue_ownership_for_incident_path(incident_path=current_path, run_id=run_id, emitted_at=emitted_at),
    )


def execute_incident_archive(
    paths: RuntimePaths,
    checkpoint: ResearchCheckpoint,
    *,
    run_id: str,
    emitted_at: datetime | None = None,
) -> IncidentArchiveExecutionResult:
    """Move one incident into the archived queue and persist closeout evidence."""

    emitted_at = emitted_at or datetime.now(timezone.utc)
    source_path, document = resolve_incident_source(paths, checkpoint)
    current_path = source_path
    if source_path.parent.name != IncidentLifecycleStatus.ARCHIVED.value:
        current_path = _move_incident(
            source_path,
            paths.agents_dir / "ideas" / "incidents" / "archived" / source_path.name,
        )
    lineage_path, lineage_record = _updated_lineage_record(
        paths=paths,
        checkpoint=checkpoint,
        document=document,
        source_path=source_path,
        current_path=current_path,
        stage="incident_archive",
        updated_at=emitted_at,
    )
    record_path = _incident_record_path(paths, stage="archive", run_id=run_id)
    source_task, blocker_ledger_path, blocker_item_key, parent_handoff_id, parent_run_id = _lineage_context(checkpoint)
    evidence_paths = _incident_archive_evidence_paths(
        paths,
        run_id=run_id,
        lineage_path=lineage_path,
    )
    _write_json_model(
        record_path,
        IncidentArchiveRecord(
            run_id=run_id,
            emitted_at=emitted_at,
            incident_id=lineage_record.incident_id,
            title=document.title,
            source_path=_relative_path(source_path, relative_to=paths.root),
            archived_path=_relative_path(current_path, relative_to=paths.root),
            lineage_path=_relative_path(lineage_path, relative_to=paths.root),
            evidence_paths=evidence_paths,
            source_task=source_task,
            blocker_ledger_path=blocker_ledger_path,
            blocker_item_key=blocker_item_key,
            parent_handoff_id=parent_handoff_id,
            parent_run_id=parent_run_id,
        ),
    )
    return IncidentArchiveExecutionResult(
        record_path=_relative_path(record_path, relative_to=paths.root),
        lineage_path=_relative_path(lineage_path, relative_to=paths.root),
        archived_path=_relative_path(current_path, relative_to=paths.root),
        queue_ownership=_queue_ownership_for_incident_path(incident_path=current_path, run_id=run_id, emitted_at=emitted_at),
    )


def execute_incident_task_generation(
    paths: RuntimePaths,
    checkpoint: ResearchCheckpoint,
    *,
    dispatch: "CompiledResearchDispatch",
    run_id: str,
    emitted_at: datetime | None = None,
    remediation_record_path: str | Path | None = None,
) -> IncidentTaskGenerationExecutionResult:
    """Run Taskmaster and Taskaudit for an incident-produced remediation package."""

    from .state import ResearchQueueFamily, ResearchQueueOwnership
    from .taskaudit import execute_taskaudit
    from .taskmaster import execute_taskmaster

    emitted_at = emitted_at or datetime.now(timezone.utc)
    record_path = _resolve_path_token(
        remediation_record_path or _incident_remediation_record_path(paths, run_id=run_id),
        relative_to=paths.root,
    )
    remediation_record = _load_incident_remediation_record(record_path)
    reviewed_spec_path = _resolve_path_token(remediation_record.fix_spec.reviewed_path, relative_to=paths.root)
    taskmaster_checkpoint = checkpoint.model_copy(
        update={
            "node_id": "taskmaster",
            "stage_kind_id": "research.taskmaster",
            "updated_at": emitted_at,
            "owned_queues": (
                ResearchQueueOwnership(
                    family=ResearchQueueFamily.GOALSPEC,
                    queue_path=reviewed_spec_path.parent,
                    item_path=reviewed_spec_path,
                    owner_token=run_id,
                    acquired_at=emitted_at,
                ),
            ),
        }
    )
    taskmaster_result = execute_taskmaster(
        paths,
        taskmaster_checkpoint,
        dispatch=dispatch,
        run_id=run_id,
        emitted_at=emitted_at,
    )
    taskaudit_result = execute_taskaudit(
        paths,
        run_id=run_id,
        emitted_at=emitted_at,
    )
    _write_json_model(
        record_path,
        remediation_record.model_copy(
            update={
                "taskmaster_record_path": taskmaster_result.record_path,
                "taskaudit_record_path": taskaudit_result.record_path,
                "task_provenance_path": taskaudit_result.provenance_path,
            }
        ),
    )
    persisted_remediation_record = _load_incident_remediation_record(record_path)
    _persist_recovery_decision(
        paths,
        checkpoint,
        emitted_at=emitted_at,
        remediation_record=persisted_remediation_record,
        remediation_record_path=_relative_path(record_path, relative_to=paths.root),
        taskaudit_record_path=taskaudit_result.record_path,
        task_provenance_path=taskaudit_result.provenance_path,
        pending_card_count=taskaudit_result.pending_card_count,
        backlog_card_count=taskaudit_result.backlog_card_count,
    )
    return IncidentTaskGenerationExecutionResult(
        remediation_record_path=_relative_path(record_path, relative_to=paths.root),
        taskmaster_record_path=taskmaster_result.record_path,
        taskaudit_record_path=taskaudit_result.record_path,
    )
__all__ = [
    "IncidentArchiveExecutionResult",
    "IncidentArchiveRecord",
    "IncidentDocument",
    "IncidentExecutionError",
    "IncidentIntakeExecutionResult",
    "IncidentIntakeRecord",
    "IncidentLifecycleStatus",
    "IncidentLineageRecord",
    "IncidentRemediationRecord",
    "IncidentRecurrenceLedger",
    "IncidentRecurrenceObservation",
    "IncidentRecurrenceRecord",
    "IncidentResolveExecutionResult",
    "IncidentResolveRecord",
    "IncidentSeverity",
    "IncidentTaskGenerationExecutionResult",
    "default_incident_recurrence_ledger",
    "execute_incident_archive",
    "execute_incident_intake",
    "execute_incident_resolve",
    "execute_incident_task_generation",
    "find_equivalent_incident",
    "incident_source_exists",
    "incident_dedup_signature",
    "load_incident_document",
    "load_incident_recurrence_ledger",
    "parse_incident_document",
    "record_incident_recurrence",
    "resolve_deduplicated_incident_path",
    "resolve_incident_source",
    "write_incident_recurrence_ledger",
]
