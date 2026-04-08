"""Incident lineage, archive evidence, and recovery-state helpers."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from ..contracts import ResearchRecoveryDecision
from ..paths import RuntimePaths
from ..queue import load_research_recovery_latch, write_research_recovery_latch

if TYPE_CHECKING:
    from .incidents import IncidentDocument, IncidentLineageRecord, IncidentRemediationRecord
    from .state import ResearchCheckpoint


def load_existing_lineage(path: Path) -> "IncidentLineageRecord | None":
    """Load an existing incident lineage record when present."""

    from .incidents import IncidentExecutionError, IncidentLineageRecord

    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise IncidentExecutionError(f"{path.as_posix()} must contain a JSON object")
    return IncidentLineageRecord.model_validate(payload)


def lineages_context(
    checkpoint: "ResearchCheckpoint",
) -> tuple[str | None, str | None, str | None, str | None, str | None]:
    """Collect blocker and handoff lineage fields for persisted incident records."""

    from .incidents import _relative_path

    active_request = checkpoint.active_request
    if active_request is None:
        return None, None, None, None, None
    blocker_record = active_request.blocker_record
    handoff = checkpoint.parent_handoff or active_request.handoff
    source_task = None
    blocker_ledger_path = None
    blocker_item_key = None
    if blocker_record is not None:
        source_task = blocker_record.source_task
        blocker_ledger_path = _relative_path(blocker_record.ledger_path, relative_to=blocker_record.ledger_path.parent.parent)
        blocker_item_key = blocker_record.item_key
    elif handoff is not None:
        source_task = handoff.task_id
    return (
        source_task,
        blocker_ledger_path,
        blocker_item_key,
        None if handoff is None else handoff.handoff_id,
        None if handoff is None or handoff.parent_run is None else handoff.parent_run.run_id,
    )


def updated_lineage_record(
    *,
    paths: RuntimePaths,
    checkpoint: "ResearchCheckpoint",
    document: "IncidentDocument",
    source_path: Path,
    current_path: Path,
    stage: Literal["incident_intake", "incident_resolve", "incident_archive"],
    updated_at: datetime,
) -> tuple[Path, "IncidentLineageRecord"]:
    """Persist the current lineage snapshot for one incident stage transition."""

    from .incidents import (
        IncidentLifecycleStatus,
        IncidentLineageRecord,
        _incident_key,
        _incident_lineage_path,
        _relative_path,
        _write_json_model,
    )

    incident_key = _incident_key(document, source_path)
    lineage_path = _incident_lineage_path(paths, incident_key=incident_key)
    existing = load_existing_lineage(lineage_path)
    source_task, blocker_ledger_path, blocker_item_key, parent_handoff_id, parent_run_id = lineages_context(checkpoint)
    record = IncidentLineageRecord(
        incident_id=document.incident_id or source_path.stem,
        title=document.title,
        source_path=(
            existing.source_path
            if existing is not None
            else _relative_path(source_path, relative_to=paths.root)
        ),
        current_path=_relative_path(current_path, relative_to=paths.root),
        working_path=(
            _relative_path(current_path, relative_to=paths.root)
            if current_path.parent.name == IncidentLifecycleStatus.WORKING.value
            else (None if existing is None else existing.working_path)
        ),
        resolved_path=(
            _relative_path(current_path, relative_to=paths.root)
            if current_path.parent.name == IncidentLifecycleStatus.RESOLVED.value
            else (None if existing is None else existing.resolved_path)
        ),
        archived_path=(
            _relative_path(current_path, relative_to=paths.root)
            if current_path.parent.name == IncidentLifecycleStatus.ARCHIVED.value
            else (None if existing is None else existing.archived_path)
        ),
        source_task=source_task if source_task is not None else (None if existing is None else existing.source_task),
        blocker_ledger_path=(
            blocker_ledger_path
            if blocker_ledger_path is not None
            else (None if existing is None else existing.blocker_ledger_path)
        ),
        blocker_item_key=(
            blocker_item_key
            if blocker_item_key is not None
            else (None if existing is None else existing.blocker_item_key)
        ),
        parent_handoff_id=(
            parent_handoff_id
            if parent_handoff_id is not None
            else (None if existing is None else existing.parent_handoff_id)
        ),
        parent_run_id=(
            parent_run_id
            if parent_run_id is not None
            else (None if existing is None else existing.parent_run_id)
        ),
        remediation_spec_id=None if existing is None else existing.remediation_spec_id,
        remediation_record_path=None if existing is None else existing.remediation_record_path,
        last_stage=stage,
        updated_at=updated_at,
    )
    _write_json_model(lineage_path, record)
    return lineage_path, record


def incident_archive_evidence_paths(
    paths: RuntimePaths,
    *,
    run_id: str,
    lineage_path: Path,
) -> tuple[str, ...]:
    """Collect ordered archive evidence paths for one incident closeout record."""

    from .incident_remediation_helpers import load_incident_remediation_record
    from .incidents import (
        _incident_record_path,
        _incident_remediation_record_path,
        _relative_path,
        _resolve_path_token,
    )

    ordered_paths: list[Path] = []

    def _append_if_present(path: Path) -> None:
        if path.exists() and path not in ordered_paths:
            ordered_paths.append(path)

    _append_if_present(_incident_record_path(paths, stage="intake", run_id=run_id))
    _append_if_present(_incident_record_path(paths, stage="resolve", run_id=run_id))
    remediation_path = _incident_remediation_record_path(paths, run_id=run_id)
    _append_if_present(remediation_path)
    if remediation_path.exists():
        remediation_record = load_incident_remediation_record(remediation_path)
        for token in (
            remediation_record.taskmaster_record_path,
            remediation_record.taskaudit_record_path,
            remediation_record.task_provenance_path,
        ):
            if token:
                _append_if_present(_resolve_path_token(token, relative_to=paths.root))
    _append_if_present(lineage_path)
    return tuple(_relative_path(path, relative_to=paths.root) for path in ordered_paths)


def checkpoint_handoff(checkpoint: "ResearchCheckpoint"):
    """Return the authoritative handoff carried by one incident checkpoint, if any."""

    if checkpoint.parent_handoff is not None:
        return checkpoint.parent_handoff
    active_request = checkpoint.active_request
    if active_request is None:
        return None
    return active_request.handoff


def persist_recovery_decision(
    paths: RuntimePaths,
    checkpoint: "ResearchCheckpoint",
    *,
    emitted_at: datetime,
    remediation_record: "IncidentRemediationRecord",
    remediation_record_path: str,
    taskaudit_record_path: str,
    task_provenance_path: str,
    pending_card_count: int,
    backlog_card_count: int,
) -> None:
    """Persist a recovery decision onto the active research recovery latch when applicable."""

    handoff = checkpoint_handoff(checkpoint)
    if handoff is None or handoff.recovery_batch_id is None:
        return

    latch = load_research_recovery_latch(paths.research_recovery_latch_file)
    if latch is None or latch.batch_id != handoff.recovery_batch_id:
        return
    if latch.handoff is not None and latch.handoff.handoff_id != handoff.handoff_id:
        return

    decision_type = (
        "regenerated_backlog_work"
        if pending_card_count > 0
        else "durable_remediation_decision"
    )
    decision = ResearchRecoveryDecision(
        decision_type=decision_type,
        decided_at=emitted_at,
        remediation_spec_id=remediation_record.fix_spec.spec_id,
        remediation_record_path=Path(remediation_record_path),
        taskaudit_record_path=Path(taskaudit_record_path),
        task_provenance_path=Path(task_provenance_path),
        lineage_path=Path(remediation_record.lineage_path),
        pending_card_count=pending_card_count,
        backlog_card_count=backlog_card_count,
    )
    write_research_recovery_latch(
        paths.research_recovery_latch_file,
        latch.model_copy(
            update={
                "handoff": latch.handoff or handoff,
                "remediation_decision": decision,
            }
        ),
    )
