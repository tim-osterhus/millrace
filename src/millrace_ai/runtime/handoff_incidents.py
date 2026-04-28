"""Planning-handoff incident creation for routed stage results."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, TypeVar
from uuid import uuid4

from pydantic import ValidationError

from millrace_ai.contracts import (
    IncidentDecision,
    IncidentDocument,
    IncidentSeverity,
    SpecDocument,
    StageResultEnvelope,
    TaskDocument,
    WorkItemKind,
)
from millrace_ai.events import write_runtime_event
from millrace_ai.queue_store import QueueStore
from millrace_ai.router import RouterDecision
from millrace_ai.work_documents import read_work_document_as

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
    lineage = (
        _closure_target_lineage(stage_result)
        if is_closure_target
        else _source_work_item_lineage(engine, stage_result)
    )
    incident_id = (
        f"arbiter-gap-{lineage.root_spec_id}-{uuid4().hex[:8]}"
        if is_closure_target and lineage.root_spec_id is not None
        else f"incident-{stage_result.work_item_id}-{uuid4().hex[:8]}"
    )
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
            else f"Planning handoff for {stage_result.work_item_kind.value} {stage_result.work_item_id}"
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
    )
    destination = queue.enqueue_incident(incident)
    write_runtime_event(
        engine.paths,
        event_type="runtime_handoff_incident_enqueued",
        data={
            "incident_id": incident_id,
            "source_work_item_kind": stage_result.work_item_kind.value,
            "source_work_item_id": stage_result.work_item_id,
            "root_idea_id": lineage.root_idea_id,
            "root_spec_id": lineage.root_spec_id,
            "source_task_id": lineage.source_task_id,
            "source_spec_id": lineage.source_spec_id,
            "destination": str(destination.relative_to(engine.paths.root)),
        },
    )
    return destination


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


def _metadata_string(stage_result: StageResultEnvelope, key: str) -> str | None:
    value = stage_result.metadata.get(key)
    return value if isinstance(value, str) and value else None


__all__ = ["enqueue_handoff_incident"]
