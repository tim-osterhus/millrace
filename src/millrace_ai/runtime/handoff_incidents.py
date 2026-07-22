"""Planning-handoff incident creation for routed stage results."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, TypeVar
from uuid import uuid4

from pydantic import ValidationError

from millrace_ai.contracts import (
    ClosureEvidenceWindow,
    ExecutionStageName,
    ExecutionTerminalResult,
    IncidentDecision,
    IncidentDocument,
    IncidentSeverity,
    PlanningStageName,
    SpecDocument,
    StageResultEnvelope,
    TaskDocument,
    WorkItemKind,
)
from millrace_ai.contracts.router import RouterDecision
from millrace_ai.events import write_runtime_event
from millrace_ai.queue_store import QueueStore
from millrace_ai.work_documents import read_work_document_as
from millrace_ai.workspace.queue_family_interpreter import QueueFamilyInterpreter

if TYPE_CHECKING:
    from millrace_ai.runtime.engine import RuntimeEngine

_DocT = TypeVar("_DocT", TaskDocument, SpecDocument, IncidentDocument)


@dataclass(frozen=True, slots=True)
class _HandoffLineage:
    root_idea_id: str | None = None
    root_spec_id: str | None = None
    source_task_id: str | None = None
    source_spec_id: str | None = None


def enqueue_handoff_incident(
    engine: RuntimeEngine,
    *,
    decision: RouterDecision,
    stage_result: StageResultEnvelope,
) -> Path:
    queue = QueueStore(engine.paths)
    is_closure_target = _is_closure_target_result(stage_result)
    is_consultant_handoff = _is_consultant_planning_handoff(stage_result)
    trigger_metadata = (
        _consultant_synthesized_trigger_metadata(stage_result)
        if is_consultant_handoff
        else _handoff_trigger_metadata(
            engine,
            stage_result,
            runtime_created=is_closure_target,
        )
    )
    lineage = (
        _closure_target_lineage(stage_result)
        if is_closure_target
        else _source_work_item_lineage(engine, stage_result)
    )
    if is_consultant_handoff:
        authored_incident = _consultant_authored_incident(
            engine,
            stage_result=stage_result,
            lineage=lineage,
        )
        if authored_incident is not None:
            return authored_incident
    if is_closure_target:
        existing_incident = _existing_closure_remediation_incident(
            engine,
            root_spec_id=lineage.root_spec_id,
            trigger_metadata=trigger_metadata,
        )
        if existing_incident is not None:
            write_runtime_event(
                engine.paths,
                event_type="runtime_handoff_incident_deduped",
                data={
                    "source_work_item_id": stage_result.work_item_id,
                    "root_spec_id": lineage.root_spec_id,
                    "existing_destination": str(existing_incident.relative_to(engine.paths.root)),
                    "previous_arbiter_run_id": trigger_metadata.get("previous_arbiter_run_id"),
                    "previous_arbiter_request_id": trigger_metadata.get("previous_arbiter_request_id"),
                },
            )
            return existing_incident
    if is_consultant_handoff:
        existing_incident = _existing_consultant_synthesized_incident(
            engine,
            source_event_id=str(trigger_metadata["source_event_id"]),
        )
        if existing_incident is not None:
            write_runtime_event(
                engine.paths,
                event_type="runtime_handoff_incident_deduped",
                data={
                    "source_work_item_id": stage_result.work_item_id,
                    "source_event_id": trigger_metadata["source_event_id"],
                    "existing_destination": str(existing_incident.relative_to(engine.paths.root)),
                },
            )
            return existing_incident
    incident_id = (
        f"arbiter-gap-{lineage.root_spec_id}-{uuid4().hex[:8]}"
        if is_closure_target and lineage.root_spec_id is not None
        else f"incident-{stage_result.work_item_id}-{trigger_metadata['source_event_id'][:12]}"
        if is_consultant_handoff
        else f"incident-{stage_result.work_item_id}-{uuid4().hex[:8]}"
    )
    source_family_id = stage_result.work_item_family_id
    evidence_paths = list(stage_result.artifact_paths)
    for key in ("preferred_rubric_path", "preferred_verdict_path", "preferred_report_path"):
        value = _metadata_string(stage_result, key)
        if value is not None and value not in evidence_paths:
            evidence_paths.append(value)
    incident = IncidentDocument(
        incident_id=incident_id,
        title=(
            f"Arbiter remediation for {lineage.root_spec_id}"
            if is_closure_target and lineage.root_spec_id is not None
            else f"Planning handoff for {source_family_id} {stage_result.work_item_id}"
        ),
        summary=(
            (
                f"Arbiter found parity gaps for root spec {lineage.root_spec_id}; planning remediation required."
                if is_closure_target and lineage.root_spec_id is not None
                else (
                    f"Stage {stage_result.stage.value} returned {stage_result.terminal_result.value}; "
                    "planning remediation required."
                )
            )
        ),
        root_idea_id=lineage.root_idea_id,
        root_spec_id=lineage.root_spec_id,
        source_task_id=lineage.source_task_id,
        source_spec_id=lineage.source_spec_id,
        source_stage=stage_result.stage,
        source_plane=stage_result.plane,
        failure_class=decision.failure_class or (
            "arbiter_parity_gap" if is_closure_target else "consultant_needs_planning"
        ),
        severity=IncidentSeverity.HIGH,
        needs_planning=True,
        trigger_reason=decision.reason,
        observed_symptoms=stage_result.notes,
        failed_attempts=(),
        consultant_decision=IncidentDecision.NEEDS_PLANNING,
        evidence_paths=tuple(evidence_paths),
        related_run_ids=(stage_result.run_id,),
        related_stage_results=(
            engine.snapshot.last_stage_result_path,
        )
        if engine.snapshot is not None and engine.snapshot.last_stage_result_path is not None
        else (),
        references=(),
        opened_at=engine._now(),
        opened_by="runtime",
        trigger_metadata=trigger_metadata,
        created_by="millrace-runtime" if is_closure_target or is_consultant_handoff else None,
    )
    destination = queue.enqueue_incident(incident)
    write_runtime_event(
        engine.paths,
        event_type="runtime_handoff_incident_enqueued",
        data={
            "incident_id": incident_id,
            "source_work_item_family_id": source_family_id,
            "source_work_item_kind": (
                stage_result.work_item_kind.value if stage_result.work_item_kind is not None else None
            ),
            "source_work_item_id": stage_result.work_item_id,
            "root_idea_id": lineage.root_idea_id,
            "root_spec_id": lineage.root_spec_id,
            "source_task_id": lineage.source_task_id,
            "source_spec_id": lineage.source_spec_id,
            "destination": str(destination.relative_to(engine.paths.root)),
        },
    )
    return destination


def _consultant_synthesized_trigger_metadata(
    stage_result: StageResultEnvelope,
) -> dict[str, bool | str]:
    request_id = _metadata_string(stage_result, "request_id")
    identity_parts = (
        stage_result.plane.value,
        stage_result.stage.value,
        stage_result.work_item_family_id,
        stage_result.work_item_id,
        stage_result.terminal_result.value,
        stage_result.run_id,
        request_id or "",
    )
    source_event_id = hashlib.sha256("\0".join(identity_parts).encode()).hexdigest()
    metadata: dict[str, bool | str] = {
        "runtime_created": True,
        "source_event_id": source_event_id,
        "source_stage": stage_result.stage.value,
        "consultant_run_id": stage_result.run_id,
    }
    if request_id is not None:
        metadata["consultant_request_id"] = request_id
    return metadata


def _existing_consultant_synthesized_incident(
    engine: RuntimeEngine,
    *,
    source_event_id: str,
) -> Path | None:
    for directory in _incident_lifecycle_directories(engine):
        for path in sorted(directory.glob("*.md")):
            incident = _read_incident_document_at(path)
            if incident is None or incident.created_by != "millrace-runtime":
                continue
            if incident.trigger_metadata.get("source_event_id") == source_event_id:
                return path
    return None


def _consultant_authored_incident(
    engine: RuntimeEngine,
    *,
    stage_result: StageResultEnvelope,
    lineage: _HandoffLineage,
) -> Path | None:
    declared_path = _metadata_string(stage_result, "incident_path")
    if declared_path is None:
        _write_authored_incident_rejection(engine, stage_result, reason="missing_path")
        return None

    lexical_candidate: Path | None = None
    try:
        lexical_candidate = _lexically_normalized_incident_reference(engine, declared_path)
    except (ValueError, RuntimeError, OSError) as exc:
        _write_authored_incident_rejection(
            engine,
            stage_result,
            reason="invalid_path",
            declared_path=declared_path,
            diagnostic=str(exc),
            candidate_path=lexical_candidate,
        )
        return None

    if not _is_known_incident_lifecycle_parent(engine, lexical_candidate.parent):
        _write_authored_incident_rejection(
            engine,
            stage_result,
            reason="outside_workspace_incident_lifecycle",
            declared_path=declared_path,
        )
        return None
    if lexical_candidate.suffix != ".md":
        _write_authored_incident_rejection(
            engine,
            stage_result,
            reason="invalid_incident_filename",
            declared_path=declared_path,
            candidate_path=lexical_candidate,
        )
        return None
    try:
        if lexical_candidate.is_symlink():
            _write_authored_incident_rejection(
                engine,
                stage_result,
                reason="symlink_not_allowed",
                declared_path=declared_path,
                candidate_path=lexical_candidate,
            )
            return None
    except (ValueError, RuntimeError, OSError) as exc:
        _write_authored_incident_rejection(
            engine,
            stage_result,
            reason="invalid_path",
            declared_path=declared_path,
            diagnostic=str(exc),
            candidate_path=lexical_candidate,
        )
        return None

    candidate = _incident_path_for_filename(engine, lexical_candidate.name)
    if candidate is None:
        _write_authored_incident_rejection(
            engine,
            stage_result,
            reason="missing_or_invalid_document",
            declared_path=declared_path,
            candidate_path=lexical_candidate,
        )
        return None
    incident = _read_incident_document_at(candidate)
    if incident is None:
        _write_authored_incident_rejection(
            engine,
            stage_result,
            reason="missing_or_invalid_document",
            declared_path=declared_path,
            candidate_path=lexical_candidate,
        )
        return None
    if incident.incident_id != candidate.stem:
        _write_authored_incident_rejection(
            engine,
            stage_result,
            reason="incident_id_path_mismatch",
            declared_path=declared_path,
            candidate_path=lexical_candidate,
        )
        return None

    mismatch = _authored_incident_mismatch(incident, stage_result=stage_result, lineage=lineage)
    if mismatch is not None:
        _write_authored_incident_rejection(
            engine,
            stage_result,
            reason="source_mismatch",
            declared_path=declared_path,
            diagnostic=mismatch,
            candidate_path=lexical_candidate,
        )
        return None

    write_runtime_event(
        engine.paths,
        event_type="runtime_handoff_incident_adopted",
        data={
            "incident_id": incident.incident_id,
            "source_work_item_id": stage_result.work_item_id,
            "declared_path": declared_path,
            "destination": str(candidate.relative_to(engine.paths.root)),
        },
    )
    return candidate


def _lexically_normalized_incident_reference(
    engine: RuntimeEngine,
    declared_path: str,
) -> Path:
    if "\0" in declared_path:
        raise ValueError("embedded null byte")
    candidate = Path(declared_path).expanduser()
    if not candidate.is_absolute():
        candidate = engine.paths.root / candidate
    return Path(os.path.abspath(os.fspath(candidate)))


def _is_known_incident_lifecycle_parent(engine: RuntimeEngine, parent: Path) -> bool:
    return parent in {
        Path(os.path.abspath(os.fspath(directory)))
        for directory in _incident_lifecycle_directories(engine)
    }


def _incident_path_for_filename(engine: RuntimeEngine, filename: str) -> Path | None:
    for directory in _incident_lifecycle_directories(engine):
        candidate = directory / filename
        try:
            if candidate.suffix != ".md" or candidate.is_symlink():
                continue
            resolved_candidate = candidate.resolve()
            if (
                resolved_candidate.suffix == ".md"
                and resolved_candidate.parent == directory.resolve()
                and resolved_candidate.is_file()
            ):
                return resolved_candidate
        except (ValueError, RuntimeError, OSError):
            continue
    return None


def _authored_incident_mismatch(
    incident: IncidentDocument,
    *,
    stage_result: StageResultEnvelope,
    lineage: _HandoffLineage,
) -> str | None:
    if incident.source_stage != stage_result.stage or incident.source_plane != stage_result.plane:
        return "source stage or plane does not match terminal event"
    if (
        incident.root_idea_id is not None
        and lineage.root_idea_id is not None
        and incident.root_idea_id != lineage.root_idea_id
    ):
        return "root idea does not match terminal event"
    if (
        incident.root_spec_id is not None
        and lineage.root_spec_id is not None
        and incident.root_spec_id != lineage.root_spec_id
    ):
        return "root spec does not match terminal event"
    if lineage.source_task_id is not None and incident.source_task_id != lineage.source_task_id:
        return "source task does not match terminal event"
    if lineage.source_spec_id is not None and incident.source_spec_id != lineage.source_spec_id:
        return "source spec does not match terminal event"
    if not incident.needs_planning or incident.consultant_decision is not IncidentDecision.NEEDS_PLANNING:
        return "incident does not declare a planning escalation"
    if incident.related_run_ids and stage_result.run_id not in incident.related_run_ids:
        return "related run does not match terminal event"
    request_id = _metadata_string(stage_result, "request_id")
    incident_request_id = incident.trigger_metadata.get("request_id") or incident.trigger_metadata.get(
        "consultant_request_id"
    )
    if (
        request_id is not None
        and isinstance(incident_request_id, str)
        and incident_request_id != request_id
    ):
        return "request does not match terminal event"
    return None


def _write_authored_incident_rejection(
    engine: RuntimeEngine,
    stage_result: StageResultEnvelope,
    *,
    reason: str,
    declared_path: str | None = None,
    diagnostic: str | None = None,
    candidate_path: Path | None = None,
) -> None:
    quarantine_destination = _quarantine_rejected_incoming_incident(
        engine,
        candidate_path,
        diagnostic=diagnostic or reason,
    )
    data: dict[str, bool | str | None] = {
        "source_work_item_id": stage_result.work_item_id,
        "declared_path": declared_path,
        "reason": reason,
        "diagnostic": diagnostic,
    }
    if quarantine_destination is not None:
        data["quarantine_destination"] = str(
            quarantine_destination.relative_to(engine.paths.root)
        )
    write_runtime_event(
        engine.paths,
        event_type="runtime_handoff_incident_authored_rejected",
        data=data,
    )


def _quarantine_rejected_incoming_incident(
    engine: RuntimeEngine,
    candidate_path: Path | None,
    *,
    diagnostic: str,
) -> Path | None:
    if candidate_path is None:
        return None
    try:
        if (
            candidate_path.parent
            != Path(os.path.abspath(os.fspath(engine.paths.incidents_incoming_dir)))
            or not (candidate_path.is_symlink() or candidate_path.is_file())
        ):
            return None
    except (ValueError, RuntimeError, OSError):
        return None
    return QueueFamilyInterpreter(engine.paths).quarantine_invalid_artifact(
        "incident",
        candidate_path,
        diagnostic,
    )


def _handoff_trigger_metadata(
    engine: RuntimeEngine,
    stage_result: StageResultEnvelope,
    *,
    runtime_created: bool,
) -> dict[str, bool | str]:
    if not runtime_created:
        return {}
    metadata: dict[str, bool | str] = {
        "runtime_created": runtime_created,
        "source_stage": stage_result.stage.value,
        "arbiter_run_id": stage_result.run_id,
    }
    request_id = _metadata_string(stage_result, "request_id")
    if request_id is not None:
        metadata["arbiter_request_id"] = request_id
    root_spec_id = _metadata_string(stage_result, "closure_target_root_spec_id")
    if root_spec_id is not None:
        metadata["closure_root_spec_id"] = root_spec_id
    previous_arbiter = _closure_previous_arbiter(engine, stage_result)
    if previous_arbiter.run_id is not None:
        metadata["previous_arbiter_run_id"] = previous_arbiter.run_id
    if previous_arbiter.request_id is not None:
        metadata["previous_arbiter_request_id"] = previous_arbiter.request_id
    return metadata


@dataclass(frozen=True, slots=True)
class _PreviousArbiter:
    run_id: str | None = None
    request_id: str | None = None


def _closure_previous_arbiter(
    engine: RuntimeEngine,
    stage_result: StageResultEnvelope,
) -> _PreviousArbiter:
    window_path = _metadata_string(stage_result, "closure_evidence_window_path")
    if window_path is None:
        return _PreviousArbiter()
    path = Path(window_path).expanduser()
    if not path.is_absolute():
        path = engine.paths.root / path
    if not path.is_file():
        return _PreviousArbiter()
    try:
        window = ClosureEvidenceWindow.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError, ValueError):
        return _PreviousArbiter()
    return _PreviousArbiter(
        run_id=window.previous_arbiter.run_id,
        request_id=window.previous_arbiter.request_id,
    )


def _existing_closure_remediation_incident(
    engine: RuntimeEngine,
    *,
    root_spec_id: str | None,
    trigger_metadata: dict[str, bool | str],
) -> Path | None:
    if root_spec_id is None:
        return None
    previous_run_id = trigger_metadata.get("previous_arbiter_run_id")
    previous_request_id = trigger_metadata.get("previous_arbiter_request_id")
    if previous_run_id is None and previous_request_id is None:
        return None
    for directory in _incident_lifecycle_directories(engine):
        for path in sorted(directory.glob("*.md")):
            incident = _read_incident_document_at(path)
            if incident is None:
                continue
            if incident.root_spec_id != root_spec_id:
                continue
            if incident.source_stage is not PlanningStageName.ARBITER:
                continue
            existing_metadata = incident.trigger_metadata
            if existing_metadata.get("runtime_created") is not True:
                continue
            if previous_run_id is not None and existing_metadata.get("previous_arbiter_run_id") != previous_run_id:
                continue
            if (
                previous_request_id is not None
                and existing_metadata.get("previous_arbiter_request_id") != previous_request_id
            ):
                continue
            return path
    return None


def _closure_target_lineage(stage_result: StageResultEnvelope) -> _HandoffLineage:
    root_spec_id = _metadata_string(stage_result, "closure_target_root_spec_id")
    return _HandoffLineage(
        root_idea_id=_metadata_string(stage_result, "closure_target_root_idea_id"),
        root_spec_id=root_spec_id,
        source_spec_id=root_spec_id,
    )


def _source_work_item_lineage(
    engine: RuntimeEngine,
    stage_result: StageResultEnvelope,
) -> _HandoffLineage:
    if stage_result.work_item_kind is WorkItemKind.TASK:
        task = _read_task_document(engine, stage_result.work_item_id)
        source_spec_id = _task_source_spec_id(task)
        return _HandoffLineage(
            root_idea_id=task.root_idea_id if task is not None else None,
            root_spec_id=source_spec_id,
            source_task_id=stage_result.work_item_id,
            source_spec_id=source_spec_id,
        )
    if stage_result.work_item_kind is WorkItemKind.SPEC:
        spec = _read_spec_document(engine, stage_result.work_item_id)
        return _HandoffLineage(
            root_idea_id=spec.root_idea_id if spec is not None else None,
            root_spec_id=_spec_root_spec_id(spec) if spec is not None else stage_result.work_item_id,
            source_spec_id=stage_result.work_item_id,
        )
    if stage_result.work_item_kind is WorkItemKind.INCIDENT:
        incident = _read_incident_document(engine, stage_result.work_item_id)
        if incident is None:
            return _HandoffLineage()
        return _HandoffLineage(
            root_idea_id=incident.root_idea_id,
            root_spec_id=incident.root_spec_id or incident.source_spec_id,
            source_task_id=incident.source_task_id,
            source_spec_id=incident.source_spec_id,
        )
    return _HandoffLineage()


def _read_task_document(engine: RuntimeEngine, task_id: str) -> TaskDocument | None:
    return _read_first_existing_document(
        (
            engine.paths.tasks_active_dir / f"{task_id}.md",
            engine.paths.tasks_queue_dir / f"{task_id}.md",
            engine.paths.tasks_blocked_dir / f"{task_id}.md",
            engine.paths.tasks_done_dir / f"{task_id}.md",
        ),
        model=TaskDocument,
    )


def _read_spec_document(engine: RuntimeEngine, spec_id: str) -> SpecDocument | None:
    return _read_first_existing_document(
        (
            engine.paths.specs_active_dir / f"{spec_id}.md",
            engine.paths.specs_queue_dir / f"{spec_id}.md",
            engine.paths.specs_blocked_dir / f"{spec_id}.md",
            engine.paths.specs_done_dir / f"{spec_id}.md",
        ),
        model=SpecDocument,
    )


def _read_incident_document(engine: RuntimeEngine, incident_id: str) -> IncidentDocument | None:
    return _read_first_existing_document(
        (
            engine.paths.incidents_active_dir / f"{incident_id}.md",
            engine.paths.incidents_incoming_dir / f"{incident_id}.md",
            engine.paths.incidents_blocked_dir / f"{incident_id}.md",
            engine.paths.incidents_resolved_dir / f"{incident_id}.md",
        ),
        model=IncidentDocument,
    )


def _read_incident_document_at(path: Path) -> IncidentDocument | None:
    try:
        return read_work_document_as(path, model=IncidentDocument)
    except (OSError, ValidationError, ValueError):
        return None


def _read_first_existing_document(paths: tuple[Path, ...], *, model: type[_DocT]) -> _DocT | None:
    for path in paths:
        try:
            return read_work_document_as(path, model=model)
        except FileNotFoundError:
            continue
        except (ValidationError, ValueError):
            continue
    return None


def _task_source_spec_id(task: TaskDocument | None) -> str | None:
    if task is None:
        return None
    return task.root_spec_id or task.spec_id


def _spec_root_spec_id(spec: SpecDocument) -> str:
    return spec.root_spec_id or spec.spec_id


def _is_closure_target_result(stage_result: StageResultEnvelope) -> bool:
    return stage_result.metadata.get("request_kind") == "closure_target"


def _is_consultant_planning_handoff(stage_result: StageResultEnvelope) -> bool:
    return (
        stage_result.stage is ExecutionStageName.CONSULTANT
        and stage_result.terminal_result is ExecutionTerminalResult.NEEDS_PLANNING
    )


def _incident_lifecycle_directories(engine: RuntimeEngine) -> tuple[Path, ...]:
    return (
        engine.paths.incidents_incoming_dir,
        engine.paths.incidents_active_dir,
        engine.paths.incidents_blocked_dir,
        engine.paths.incidents_resolved_dir,
    )


def _metadata_string(stage_result: StageResultEnvelope, key: str) -> str | None:
    value = stage_result.metadata.get(key)
    return value if isinstance(value, str) and value else None


__all__ = ["enqueue_handoff_incident"]
