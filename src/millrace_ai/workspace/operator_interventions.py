"""Audited operator interventions for bad intake and stale queue state."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePath
from typing import Literal
from uuid import uuid4

from pydantic import model_validator

from millrace_ai.architecture import WorkItemFamilyDefinition
from millrace_ai.assets import load_builtin_workflow_primitives
from millrace_ai.compilation.persistence import load_existing_plan
from millrace_ai.contracts import ContractModel, TaskDocument, WorkItemKind
from millrace_ai.contracts.work_refs import (
    coerce_family_and_kind,
    family_id_for_work_item_kind,
    normalize_work_item_family_id,
)
from millrace_ai.errors import QueueStateError
from millrace_ai.events import write_runtime_event

from .paths import WorkspacePaths
from .work_documents import read_work_document_as, render_work_document

InterventionAction = Literal[
    "cancel",
    "archive_blocked_task",
    "supersede",
    "retarget_dependency",
    "resolve_incident",
    "cancel_incident",
    "archive_invalid_incident",
]

TaskSupersedeCascade = Literal["none", "retarget", "cancel"]


class OperatorInterventionRecord(ContractModel):
    """Persisted audit record for one runtime-owned operator intervention."""

    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["operator_intervention"] = "operator_intervention"

    action: InterventionAction
    actor: str
    reason: str
    issued_at: datetime
    applied_at: datetime
    work_item_family_id: str | None = None
    work_item_kind: WorkItemKind | None = None
    work_item_id: str
    source_state: str
    destination_state: str
    source_path: str
    destination_path: str
    replacement_work_item_id: str | None = None
    affected_dependents: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_operator_record(self) -> "OperatorInterventionRecord":
        if self.work_item_family_id is None and self.work_item_kind is not None:
            self.work_item_family_id = family_id_for_work_item_kind(self.work_item_kind)
        if self.work_item_family_id is None:
            raise ValueError("work_item_family_id or work_item_kind is required")
        self.work_item_family_id = normalize_work_item_family_id(self.work_item_family_id)
        if not self.actor.strip():
            raise ValueError("actor is required")
        if not self.reason.strip():
            raise ValueError("reason is required")
        if not self.work_item_id.strip():
            raise ValueError("work_item_id is required")
        if self.applied_at.tzinfo is None or self.applied_at.utcoffset() is None:
            raise ValueError("applied_at must be timezone-aware")
        if self.issued_at.tzinfo is None or self.issued_at.utcoffset() is None:
            raise ValueError("issued_at must be timezone-aware")
        return self


class OperatorInterventionResult(ContractModel):
    """In-memory result returned by one operator intervention."""

    action: InterventionAction
    work_item_family_id: str
    work_item_kind: WorkItemKind | None = None
    work_item_id: str
    source_state: str
    destination_state: str
    source_path: Path
    destination_path: Path
    event_type: str
    replacement_work_item_id: str | None = None
    affected_dependents: tuple[str, ...] = ()
    record: OperatorInterventionRecord


@dataclass(frozen=True, slots=True)
class _LocatedItem:
    work_item_family_id: str
    work_item_kind: WorkItemKind | None
    work_item_id: str
    state: str
    path: Path
    cancel_archive_dir: Path | None = None
    cancel_destination_state: str | None = None


def cancel_work_item(
    paths: WorkspacePaths,
    *,
    work_item_id: str,
    work_item_family_id: str | None = None,
    work_item_kind: WorkItemKind | None = None,
    reason: str,
    actor: str = "operator",
    now: datetime | None = None,
    issued_at: datetime | None = None,
    force: bool = False,
) -> OperatorInterventionResult:
    """Cancel one queued or blocked work item without deleting its document."""

    del force  # Reserved for duplicate/lineage warning overrides in a later UI.
    work_item_family_id, work_item_kind = coerce_family_and_kind(
        family_id=work_item_family_id,
        work_item_kind=work_item_kind,
    )
    located = _locate_cancelable_work_item(
        paths,
        work_item_id=work_item_id,
        work_item_family_id=work_item_family_id,
        work_item_kind=work_item_kind,
    )
    return _archive_located_item(
        paths,
        located,
        action="cancel",
        destination_state=located.cancel_destination_state or "cancelled",
        archive_name="cancelled",
        event_type="work_item_cancelled",
        reason=reason,
        actor=actor,
        now=now,
        issued_at=issued_at,
    )


def archive_blocked_task(
    paths: WorkspacePaths,
    *,
    task_id: str,
    reason: str,
    actor: str = "operator",
    now: datetime | None = None,
    issued_at: datetime | None = None,
) -> OperatorInterventionResult:
    """Archive a blocked task that should not be retried."""

    located = _locate_exact_task(paths, task_id=task_id, states=("blocked",))
    return _archive_located_item(
        paths,
        located,
        action="archive_blocked_task",
        destination_state="cancelled",
        archive_name="cancelled",
        event_type="blocked_task_archived",
        reason=reason,
        actor=actor,
        now=now,
        issued_at=issued_at,
    )


def supersede_task(
    paths: WorkspacePaths,
    *,
    old_task_id: str,
    replacement_task_id: str,
    reason: str,
    actor: str = "operator",
    cascade: TaskSupersedeCascade = "none",
    now: datetime | None = None,
    issued_at: datetime | None = None,
) -> OperatorInterventionResult:
    """Supersede a queued or blocked task with an existing replacement task."""

    if cascade not in {"none", "retarget", "cancel"}:
        raise QueueStateError("cascade must be one of: none, retarget, cancel")
    if old_task_id == replacement_task_id:
        raise QueueStateError("replacement task must be different from superseded task")

    located = _locate_exact_task(paths, task_id=old_task_id, states=("blocked", "queue"))
    _require_replacement_task(paths, replacement_task_id)
    affected_dependents = _queued_dependents(paths, old_task_id)
    result = _archive_located_item(
        paths,
        located,
        action="supersede",
        destination_state="superseded",
        archive_name="superseded",
        event_type="task_superseded",
        reason=reason,
        actor=actor,
        now=now,
        issued_at=issued_at,
        replacement_work_item_id=replacement_task_id,
        affected_dependents=tuple(dependent.task_id for dependent in affected_dependents),
    )

    if cascade == "retarget":
        for dependent in affected_dependents:
            retarget_queued_task_dependency(
                paths,
                task_id=dependent.task_id,
                old_dependency_id=old_task_id,
                new_dependency_id=replacement_task_id,
                reason=reason,
                actor=actor,
                now=now,
                issued_at=issued_at,
            )
    elif cascade == "cancel":
        for dependent in affected_dependents:
            cancel_work_item(
                paths,
                work_item_id=dependent.task_id,
                work_item_kind=WorkItemKind.TASK,
                reason=reason,
                actor=actor,
                now=now,
                issued_at=issued_at,
            )

    return result


def retarget_queued_task_dependency(
    paths: WorkspacePaths,
    *,
    task_id: str,
    old_dependency_id: str,
    new_dependency_id: str,
    reason: str,
    actor: str = "operator",
    now: datetime | None = None,
    issued_at: datetime | None = None,
) -> OperatorInterventionResult:
    """Rewrite one queued task dependency from an old task id to a replacement."""

    applied_at = _coerce_now(now)
    cleaned_reason = _clean_reason(reason)
    task_path = paths.tasks_queue_dir / f"{task_id}.md"
    if not task_path.is_file():
        raise QueueStateError(f"task {task_id} is not queued")
    _require_replacement_task(paths, new_dependency_id)

    task = read_work_document_as(task_path, model=TaskDocument)
    if old_dependency_id not in task.depends_on:
        raise QueueStateError(f"task {task_id} does not depend on {old_dependency_id}")
    updated_dependencies = tuple(new_dependency_id if value == old_dependency_id else value for value in task.depends_on)
    updated = task.model_copy(update={"depends_on": updated_dependencies, "updated_at": applied_at})
    task_path.write_text(render_work_document(updated), encoding="utf-8")

    record = _record(
        paths,
        action="retarget_dependency",
        work_item_kind=WorkItemKind.TASK,
        work_item_id=task_id,
        source_state="queue",
        destination_state="queue",
        source_path=task_path,
        destination_path=task_path,
        reason=cleaned_reason,
        actor=actor,
        issued_at=_coerce_now(issued_at) if issued_at is not None else applied_at,
        applied_at=applied_at,
        replacement_work_item_id=new_dependency_id,
        affected_dependents=(task_id,),
    )
    _append_record(paths.tasks_queue_dir / "interventions.jsonl", record)
    _emit_event(paths, "task_dependency_retargeted", record)
    return OperatorInterventionResult(
        action="retarget_dependency",
        work_item_family_id=_record_family_id(record),
        work_item_kind=WorkItemKind.TASK,
        work_item_id=task_id,
        source_state="queue",
        destination_state="queue",
        source_path=task_path,
        destination_path=task_path,
        event_type="task_dependency_retargeted",
        replacement_work_item_id=new_dependency_id,
        affected_dependents=(task_id,),
        record=record,
    )


def resolve_incident_by_operator(
    paths: WorkspacePaths,
    *,
    incident_id: str,
    reason: str,
    actor: str = "operator",
    now: datetime | None = None,
    issued_at: datetime | None = None,
) -> OperatorInterventionResult:
    """Close an incident as operator-resolved."""

    located = _locate_incident(paths, incident_id=incident_id, states=("incoming", "active", "blocked"))
    return _archive_located_item(
        paths,
        located,
        action="resolve_incident",
        destination_state="resolved",
        archive_name="operator",
        event_type="incident_resolved_by_operator",
        reason=reason,
        actor=actor,
        now=now,
        issued_at=issued_at,
        explicit_archive_parent=paths.incidents_resolved_dir,
    )


def cancel_incident(
    paths: WorkspacePaths,
    *,
    incident_id: str,
    reason: str,
    actor: str = "operator",
    now: datetime | None = None,
    issued_at: datetime | None = None,
) -> OperatorInterventionResult:
    """Cancel an incoming, active, or blocked incident without resolving it."""

    located = _locate_incident(paths, incident_id=incident_id, states=("incoming", "active", "blocked"))
    return _archive_located_item(
        paths,
        located,
        action="cancel_incident",
        destination_state="cancelled",
        archive_name="cancelled",
        event_type="incident_cancelled",
        reason=reason,
        actor=actor,
        now=now,
        issued_at=issued_at,
    )


def archive_invalid_incident_artifact(
    paths: WorkspacePaths,
    *,
    filename: str,
    reason: str,
    actor: str = "operator",
    now: datetime | None = None,
    issued_at: datetime | None = None,
) -> OperatorInterventionResult:
    """Archive an invalid incoming incident artifact by filename."""

    applied_at = _coerce_now(now)
    cleaned_reason = _clean_reason(reason)
    source_name = _validate_single_relative_filename(filename)
    source = paths.incidents_incoming_dir / source_name
    if not source.is_file():
        raise QueueStateError(f"invalid incident artifact not found: {source_name}")
    if not source.name.endswith(".invalid") and not _invalid_artifacts_log_mentions(paths, source.name):
        raise QueueStateError("invalid incident artifact must end with .invalid or be listed in invalid-artifacts.jsonl")

    destination_dir = paths.incidents_incoming_dir / "invalid-archived"
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / f"{source.name}.{_archive_suffix(applied_at)}"
    source.replace(destination)

    record = _record(
        paths,
        action="archive_invalid_incident",
        work_item_kind=WorkItemKind.INCIDENT,
        work_item_id=source.name,
        source_state="incoming_invalid",
        destination_state="archived",
        source_path=source,
        destination_path=destination,
        reason=cleaned_reason,
        actor=actor,
        issued_at=_coerce_now(issued_at) if issued_at is not None else applied_at,
        applied_at=applied_at,
    )
    _append_record(destination_dir / "interventions.jsonl", record)
    _emit_event(paths, "invalid_incident_artifact_archived", record)
    return OperatorInterventionResult(
        action="archive_invalid_incident",
        work_item_family_id=_record_family_id(record),
        work_item_kind=WorkItemKind.INCIDENT,
        work_item_id=source.name,
        source_state="incoming_invalid",
        destination_state="archived",
        source_path=source,
        destination_path=destination,
        event_type="invalid_incident_artifact_archived",
        record=record,
    )


def _archive_located_item(
    paths: WorkspacePaths,
    located: _LocatedItem,
    *,
    action: InterventionAction,
    destination_state: str,
    archive_name: str,
    event_type: str,
    reason: str,
    actor: str,
    now: datetime | None,
    issued_at: datetime | None,
    replacement_work_item_id: str | None = None,
    affected_dependents: tuple[str, ...] = (),
    explicit_archive_parent: Path | None = None,
) -> OperatorInterventionResult:
    applied_at = _coerce_now(now)
    cleaned_reason = _clean_reason(reason)
    if action == "cancel" and located.cancel_archive_dir is not None:
        archive_dir = located.cancel_archive_dir
    else:
        archive_parent = explicit_archive_parent or located.path.parent
        archive_dir = archive_parent / archive_name
    archive_dir.mkdir(parents=True, exist_ok=True)
    destination = archive_dir / (
        f"{located.work_item_id}.{_archive_suffix(applied_at)}.{located.state}{located.path.suffix}"
    )
    if destination.exists():
        raise QueueStateError(f"archive destination already exists: {destination}")
    located.path.replace(destination)

    record = _record(
        paths,
        action=action,
        work_item_family_id=located.work_item_family_id,
        work_item_kind=located.work_item_kind,
        work_item_id=located.work_item_id,
        source_state=located.state,
        destination_state=destination_state,
        source_path=located.path,
        destination_path=destination,
        reason=cleaned_reason,
        actor=actor,
        issued_at=_coerce_now(issued_at) if issued_at is not None else applied_at,
        applied_at=applied_at,
        replacement_work_item_id=replacement_work_item_id,
        affected_dependents=affected_dependents,
    )
    _append_record(archive_dir / "interventions.jsonl", record)
    _emit_event(paths, event_type, record)
    return OperatorInterventionResult(
        action=action,
        work_item_family_id=located.work_item_family_id,
        work_item_kind=located.work_item_kind,
        work_item_id=located.work_item_id,
        source_state=located.state,
        destination_state=destination_state,
        source_path=located.path,
        destination_path=destination,
        event_type=event_type,
        replacement_work_item_id=replacement_work_item_id,
        affected_dependents=affected_dependents,
        record=record,
    )


def _locate_cancelable_work_item(
    paths: WorkspacePaths,
    *,
    work_item_id: str,
    work_item_family_id: str | None,
    work_item_kind: WorkItemKind | None,
) -> _LocatedItem:
    candidates: list[_LocatedItem] = []
    if work_item_family_id is not None:
        candidates.extend(
            _locate_cancelable_for_family(
                paths,
                work_item_id=work_item_id,
                family_id=work_item_family_id,
                work_item_kind=work_item_kind,
            )
        )
    else:
        kinds = tuple(WorkItemKind) if work_item_kind is None else (work_item_kind,)
        for kind in kinds:
            candidates.extend(_locate_cancelable_for_kind(paths, work_item_id=work_item_id, kind=kind))
    if not candidates:
        raise QueueStateError(f"cancelable work item not found: {work_item_id}")
    if len(candidates) > 1:
        raise QueueStateError(f"work item id is ambiguous; pass --kind for {work_item_id}")
    return candidates[0]


def _locate_cancelable_for_kind(paths: WorkspacePaths, *, work_item_id: str, kind: WorkItemKind) -> list[_LocatedItem]:
    directories: tuple[tuple[str, Path], ...]
    if kind is WorkItemKind.TASK:
        directories = (("queue", paths.tasks_queue_dir), ("blocked", paths.tasks_blocked_dir))
    elif kind is WorkItemKind.PROBE:
        directories = (("queue", paths.probes_queue_dir), ("blocked", paths.probes_blocked_dir))
    elif kind is WorkItemKind.SPEC:
        directories = (("queue", paths.specs_queue_dir), ("blocked", paths.specs_blocked_dir))
    elif kind is WorkItemKind.INCIDENT:
        directories = (("incoming", paths.incidents_incoming_dir), ("blocked", paths.incidents_blocked_dir))
    elif kind is WorkItemKind.BLUEPRINT_DRAFT:
        family = _work_item_families_by_id(paths).get(kind.value)
        if family is None:
            directories = ()
        else:
            return _locate_cancelable_for_family_definition(
                paths,
                work_item_id=work_item_id,
                family=family,
                work_item_kind=kind,
            )
    else:
        directories = ()
    return [
        _LocatedItem(kind.value, kind, work_item_id, state, directory / f"{work_item_id}.md")
        for state, directory in directories
        if (directory / f"{work_item_id}.md").is_file()
    ]


def _locate_cancelable_for_family(
    paths: WorkspacePaths,
    *,
    work_item_id: str,
    family_id: str,
    work_item_kind: WorkItemKind | None,
) -> list[_LocatedItem]:
    family = _work_item_families_by_id(paths).get(family_id)
    if family is None:
        raise QueueStateError(f"operator cancellation for family {family_id} is not supported")
    return _locate_cancelable_for_family_definition(
        paths,
        work_item_id=work_item_id,
        family=family,
        work_item_kind=work_item_kind,
    )


def _locate_cancelable_for_family_definition(
    paths: WorkspacePaths,
    *,
    work_item_id: str,
    family: WorkItemFamilyDefinition,
    work_item_kind: WorkItemKind | None,
) -> list[_LocatedItem]:
    if "cancel" not in family.operator_capabilities:
        raise QueueStateError(f"operator cancellation for family {family.family_id} is not supported")
    directories = (
        (family.claimable_state, paths.runtime_root / family.queue_dirs.queue),
        (family.blocked_state, paths.runtime_root / family.queue_dirs.blocked),
    )
    cancel_archive_dir = (
        paths.runtime_root / family.queue_dirs.canceled
        if family.queue_dirs.canceled is not None
        else None
    )
    return [
        _LocatedItem(
            family.family_id,
            work_item_kind,
            work_item_id,
            state,
            directory / f"{work_item_id}{family.file_extension}",
            cancel_archive_dir=cancel_archive_dir,
            cancel_destination_state=family.canceled_state,
        )
        for state, directory in directories
        if (directory / f"{work_item_id}{family.file_extension}").is_file()
    ]


def _locate_exact_task(paths: WorkspacePaths, *, task_id: str, states: tuple[str, ...]) -> _LocatedItem:
    directories = {
        "queue": paths.tasks_queue_dir,
        "active": paths.tasks_active_dir,
        "done": paths.tasks_done_dir,
        "blocked": paths.tasks_blocked_dir,
    }
    candidates = [
        _LocatedItem(WorkItemKind.TASK.value, WorkItemKind.TASK, task_id, state, directories[state] / f"{task_id}.md")
        for state in states
        if (directories[state] / f"{task_id}.md").is_file()
    ]
    if not candidates:
        allowed = ", ".join(states)
        raise QueueStateError(f"task {task_id} is not in an allowed state: {allowed}")
    if len(candidates) > 1:
        raise QueueStateError(f"task {task_id} exists in multiple states")
    return candidates[0]


def _locate_incident(paths: WorkspacePaths, *, incident_id: str, states: tuple[str, ...]) -> _LocatedItem:
    directories = {
        "incoming": paths.incidents_incoming_dir,
        "active": paths.incidents_active_dir,
        "resolved": paths.incidents_resolved_dir,
        "blocked": paths.incidents_blocked_dir,
    }
    candidates = [
        _LocatedItem(
            WorkItemKind.INCIDENT.value,
            WorkItemKind.INCIDENT,
            incident_id,
            state,
            directories[state] / f"{incident_id}.md",
        )
        for state in states
        if (directories[state] / f"{incident_id}.md").is_file()
    ]
    if not candidates:
        allowed = ", ".join(states)
        raise QueueStateError(f"incident {incident_id} is not in an allowed state: {allowed}")
    if len(candidates) > 1:
        raise QueueStateError(f"incident {incident_id} exists in multiple states")
    return candidates[0]


def _require_replacement_task(paths: WorkspacePaths, task_id: str) -> None:
    for directory in (paths.tasks_queue_dir, paths.tasks_active_dir, paths.tasks_done_dir):
        if (directory / f"{task_id}.md").is_file():
            return
    raise QueueStateError(f"replacement task is not queued, active, or done: {task_id}")


@dataclass(frozen=True, slots=True)
class _QueuedDependent:
    task_id: str
    path: Path


def _queued_dependents(paths: WorkspacePaths, old_task_id: str) -> tuple[_QueuedDependent, ...]:
    dependents: list[_QueuedDependent] = []
    for task_path in sorted(paths.tasks_queue_dir.glob("*.md")):
        try:
            task = read_work_document_as(task_path, model=TaskDocument)
        except (OSError, ValueError):
            continue
        if old_task_id in task.depends_on:
            dependents.append(_QueuedDependent(task_id=task.task_id, path=task_path))
    return tuple(dependents)


def _record(
    paths: WorkspacePaths,
    *,
    action: InterventionAction,
    work_item_family_id: str | None = None,
    work_item_kind: WorkItemKind | None,
    work_item_id: str,
    source_state: str,
    destination_state: str,
    source_path: Path,
    destination_path: Path,
    reason: str,
    actor: str,
    issued_at: datetime,
    applied_at: datetime,
    replacement_work_item_id: str | None = None,
    affected_dependents: tuple[str, ...] = (),
) -> OperatorInterventionRecord:
    return OperatorInterventionRecord(
        action=action,
        actor=actor,
        reason=reason,
        issued_at=issued_at,
        applied_at=applied_at,
        work_item_family_id=work_item_family_id,
        work_item_kind=work_item_kind,
        work_item_id=work_item_id,
        source_state=source_state,
        destination_state=destination_state,
        source_path=_relative(paths, source_path),
        destination_path=_relative(paths, destination_path),
        replacement_work_item_id=replacement_work_item_id,
        affected_dependents=affected_dependents,
    )


def _record_family_id(record: OperatorInterventionRecord) -> str:
    if record.work_item_family_id is None:
        raise QueueStateError("operator intervention record is missing work_item_family_id")
    return record.work_item_family_id


def _work_item_families_by_id(paths: WorkspacePaths) -> dict[str, WorkItemFamilyDefinition]:
    compiled_plan = load_existing_plan(paths.state_dir / "compiled_plan.json")
    if compiled_plan is not None and compiled_plan.work_item_families_by_id:
        return dict(compiled_plan.work_item_families_by_id)
    return {
        family.family_id: family
        for family in load_builtin_workflow_primitives().work_item_families
    }


def _append_record(path: Path, record: OperatorInterventionRecord) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record.model_dump(mode="json"), sort_keys=True) + "\n")


def _emit_event(paths: WorkspacePaths, event_type: str, record: OperatorInterventionRecord) -> None:
    write_runtime_event(paths, event_type=event_type, data=record.model_dump(mode="json"))


def _clean_reason(reason: str) -> str:
    cleaned = reason.strip()
    if not cleaned:
        raise QueueStateError("reason is required")
    return cleaned


def _coerce_now(now: datetime | None) -> datetime:
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _archive_suffix(value: datetime) -> str:
    return f"{value.strftime('%Y%m%dT%H%M%SZ')}.{uuid4().hex[:8]}"


def _relative(paths: WorkspacePaths, path: Path) -> str:
    try:
        return str(path.relative_to(paths.root))
    except ValueError:
        return str(path)


def _validate_single_relative_filename(filename: str) -> str:
    cleaned = filename.strip()
    if cleaned != filename or not cleaned:
        raise ValueError("filename must be a single relative filename")
    path = PurePath(cleaned)
    if path.is_absolute() or len(path.parts) != 1:
        raise ValueError("filename must be a single relative filename")
    return cleaned


def _invalid_artifacts_log_mentions(paths: WorkspacePaths, filename: str) -> bool:
    log_path = paths.incidents_incoming_dir / "invalid-artifacts.jsonl"
    if not log_path.is_file():
        return False
    for line in log_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and filename in json.dumps(payload):
            return True
    return False


__all__ = [
    "OperatorInterventionRecord",
    "OperatorInterventionResult",
    "TaskSupersedeCascade",
    "archive_blocked_task",
    "archive_invalid_incident_artifact",
    "cancel_incident",
    "cancel_work_item",
    "resolve_incident_by_operator",
    "retarget_queued_task_dependency",
    "supersede_task",
]
