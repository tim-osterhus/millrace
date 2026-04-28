"""Closure-lineage integrity diagnostics and safe repair helpers."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import Field, ValidationError

from millrace_ai.contracts import (
    ClosureTargetState,
    IncidentDocument,
    SpecDocument,
    TaskDocument,
    WorkItemKind,
)
from millrace_ai.contracts.base import ContractModel

from .paths import WorkspacePaths
from .work_documents import parse_work_document_as, render_work_document

LineageWorkState = Literal["queue", "active", "blocked"]
LineageDiagnosticReason = Literal[
    "same_root_idea_different_root_spec",
    "known_root_spec_alias",
]


class LineageDriftFinding(ContractModel):
    """One work document attached to the wrong root spec for an open closure target."""

    work_item_kind: WorkItemKind
    work_item_id: str
    state: LineageWorkState
    path: str
    expected_root_spec_id: str
    actual_root_spec_id: str | None = None
    root_idea_id: str | None = None
    spec_id: str | None = None
    diagnostic_reason: LineageDiagnosticReason


class LineageDriftDiagnostic(ContractModel):
    """Durable diagnostic for closure lineage drift."""

    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["closure_lineage_drift_diagnostic"] = "closure_lineage_drift_diagnostic"
    root_spec_id: str
    root_idea_id: str
    detected_at: datetime
    findings: tuple[LineageDriftFinding, ...] = Field(default_factory=tuple)
    recommended_command: str


class LineageRepairChange(ContractModel):
    """One field-level lineage repair that can be previewed or applied."""

    work_item_kind: WorkItemKind
    work_item_id: str
    state: LineageWorkState
    path: str
    field_name: str
    old_value: str | None = None
    new_value: str


class LineageRepairPlan(ContractModel):
    """Preview of safe closure-lineage repairs for queued/blocked documents."""

    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["closure_lineage_repair_plan"] = "closure_lineage_repair_plan"
    root_spec_id: str
    root_idea_id: str
    created_at: datetime
    changes: tuple[LineageRepairChange, ...] = Field(default_factory=tuple)
    skipped_findings: tuple[LineageDriftFinding, ...] = Field(default_factory=tuple)


WorkDocument = TaskDocument | SpecDocument | IncidentDocument


def effective_root_spec_id(document: WorkDocument) -> str | None:
    """Return the root-spec identity used by closure-scoped queue selection."""

    if document.root_spec_id is not None:
        return document.root_spec_id
    if isinstance(document, TaskDocument):
        return document.spec_id
    if isinstance(document, IncidentDocument):
        return document.source_spec_id
    if document.source_type in {"idea", "manual"}:
        return document.spec_id
    return None


def known_root_spec_aliases(root_spec_id: str) -> tuple[str, ...]:
    """Return deterministic diagnostic aliases for known watcher-derived root IDs."""

    aliases: list[str] = []
    if root_spec_id.startswith("idea-idea-"):
        aliases.append(root_spec_id.removeprefix("idea-"))
    return tuple(dict.fromkeys(alias for alias in aliases if alias and alias != root_spec_id))


def scan_closure_lineage_drift(
    paths: WorkspacePaths,
    target: ClosureTargetState,
    *,
    detected_at: datetime | None = None,
) -> LineageDriftDiagnostic | None:
    """Find queued/active/blocked work that is tied to a target but has the wrong root."""

    findings: list[LineageDriftFinding] = []
    aliases = set(known_root_spec_aliases(target.root_spec_id))
    for surface in _lineage_surfaces(paths):
        for path in _list_markdown_files(surface.directory):
            document = _parse_surface_document(path, surface.model)
            if document is None:
                continue
            actual_root_spec_id = effective_root_spec_id(document)
            if actual_root_spec_id == target.root_spec_id:
                continue
            reason = _drift_reason(
                document,
                target=target,
                actual_root_spec_id=actual_root_spec_id,
                aliases=aliases,
            )
            if reason is None:
                continue
            findings.append(
                LineageDriftFinding(
                    work_item_kind=surface.work_item_kind,
                    work_item_id=_document_id(document),
                    state=surface.state,
                    path=_workspace_relative_path(paths, path),
                    expected_root_spec_id=target.root_spec_id,
                    actual_root_spec_id=actual_root_spec_id,
                    root_idea_id=document.root_idea_id,
                    spec_id=_document_spec_id(document),
                    diagnostic_reason=reason,
                )
            )

    if not findings:
        return None

    findings.sort(key=lambda item: (item.path, item.work_item_id))
    return LineageDriftDiagnostic(
        root_spec_id=target.root_spec_id,
        root_idea_id=target.root_idea_id,
        detected_at=_coerce_detected_at(detected_at),
        findings=tuple(findings),
        recommended_command=(
            "millrace queue repair-lineage --workspace <workspace> "
            f"--root-spec-id {target.root_spec_id} --apply"
        ),
    )


def lineage_drift_diagnostic_path(paths: WorkspacePaths, *, root_spec_id: str) -> Path:
    """Return the durable diagnostic path for one closure target."""

    return paths.arbiter_dir / "diagnostics" / "lineage-drift" / f"{root_spec_id}.json"


def write_lineage_drift_diagnostic(
    paths: WorkspacePaths,
    diagnostic: LineageDriftDiagnostic,
) -> Path:
    """Persist a closure lineage drift diagnostic atomically."""

    path = lineage_drift_diagnostic_path(paths, root_spec_id=diagnostic.root_spec_id)
    _atomic_write_text(path, diagnostic.model_dump_json(indent=2) + "\n")
    return path


def build_lineage_repair_plan(
    paths: WorkspacePaths,
    target: ClosureTargetState,
    *,
    created_at: datetime | None = None,
) -> LineageRepairPlan:
    """Build a preview of safe queued/blocked lineage repair changes."""

    diagnostic = scan_closure_lineage_drift(paths, target, detected_at=created_at)
    if diagnostic is None:
        return LineageRepairPlan(
            root_spec_id=target.root_spec_id,
            root_idea_id=target.root_idea_id,
            created_at=_coerce_detected_at(created_at),
        )

    changes: list[LineageRepairChange] = []
    skipped: list[LineageDriftFinding] = []
    aliases = set(known_root_spec_aliases(target.root_spec_id))
    for finding in diagnostic.findings:
        if finding.state not in {"queue", "blocked"}:
            skipped.append(finding)
            continue
        if finding.work_item_kind is WorkItemKind.TASK:
            changes.extend(_task_repair_changes(finding, target=target, aliases=aliases))
            continue
        if finding.work_item_kind is WorkItemKind.INCIDENT:
            changes.extend(_incident_repair_changes(finding, target=target))
            continue
        skipped.append(finding)

    return LineageRepairPlan(
        root_spec_id=target.root_spec_id,
        root_idea_id=target.root_idea_id,
        created_at=_coerce_detected_at(created_at),
        changes=tuple(changes),
        skipped_findings=tuple(skipped),
    )


def apply_lineage_repair_plan(paths: WorkspacePaths, plan: LineageRepairPlan) -> int:
    """Apply a safe queued/blocked lineage repair plan."""

    changes_by_path: dict[str, list[LineageRepairChange]] = {}
    for change in plan.changes:
        changes_by_path.setdefault(change.path, []).append(change)

    repaired_paths = 0
    for relative_path, changes in sorted(changes_by_path.items()):
        path = paths.root / relative_path
        work_item_kind = changes[0].work_item_kind
        if work_item_kind is WorkItemKind.TASK:
            task_document = parse_work_document_as(
                path.read_text(encoding="utf-8"),
                model=TaskDocument,
                path=path,
            )
            updates = _updates_from_changes(changes)
            _atomic_write_text(path, render_work_document(task_document.model_copy(update=updates)))
            repaired_paths += 1
            continue
        if work_item_kind is WorkItemKind.INCIDENT:
            incident_document = parse_work_document_as(
                path.read_text(encoding="utf-8"),
                model=IncidentDocument,
                path=path,
            )
            updates = _updates_from_changes(changes)
            _atomic_write_text(path, render_work_document(incident_document.model_copy(update=updates)))
            repaired_paths += 1
            continue
    return repaired_paths


def write_lineage_repair_report(paths: WorkspacePaths, plan: LineageRepairPlan, *, applied: bool) -> Path:
    """Persist a durable repair report."""

    timestamp = plan.created_at.strftime("%Y%m%dT%H%M%SZ")
    path = paths.arbiter_dir / "diagnostics" / "lineage-repairs" / f"{timestamp}-{uuid4().hex[:8]}.json"
    payload = {
        **plan.model_dump(mode="json"),
        "applied": applied,
    }
    _atomic_write_text(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return path


class _LineageSurface(ContractModel):
    directory: Path
    model: type[TaskDocument] | type[SpecDocument] | type[IncidentDocument]
    work_item_kind: WorkItemKind
    state: LineageWorkState


def _lineage_surfaces(paths: WorkspacePaths) -> tuple[_LineageSurface, ...]:
    return (
        _LineageSurface(
            directory=paths.tasks_queue_dir,
            model=TaskDocument,
            work_item_kind=WorkItemKind.TASK,
            state="queue",
        ),
        _LineageSurface(
            directory=paths.tasks_active_dir,
            model=TaskDocument,
            work_item_kind=WorkItemKind.TASK,
            state="active",
        ),
        _LineageSurface(
            directory=paths.tasks_blocked_dir,
            model=TaskDocument,
            work_item_kind=WorkItemKind.TASK,
            state="blocked",
        ),
        _LineageSurface(
            directory=paths.specs_queue_dir,
            model=SpecDocument,
            work_item_kind=WorkItemKind.SPEC,
            state="queue",
        ),
        _LineageSurface(
            directory=paths.specs_active_dir,
            model=SpecDocument,
            work_item_kind=WorkItemKind.SPEC,
            state="active",
        ),
        _LineageSurface(
            directory=paths.specs_blocked_dir,
            model=SpecDocument,
            work_item_kind=WorkItemKind.SPEC,
            state="blocked",
        ),
        _LineageSurface(
            directory=paths.incidents_incoming_dir,
            model=IncidentDocument,
            work_item_kind=WorkItemKind.INCIDENT,
            state="queue",
        ),
        _LineageSurface(
            directory=paths.incidents_active_dir,
            model=IncidentDocument,
            work_item_kind=WorkItemKind.INCIDENT,
            state="active",
        ),
        _LineageSurface(
            directory=paths.incidents_blocked_dir,
            model=IncidentDocument,
            work_item_kind=WorkItemKind.INCIDENT,
            state="blocked",
        ),
    )


def _parse_surface_document(
    path: Path,
    model: type[TaskDocument] | type[SpecDocument] | type[IncidentDocument],
) -> WorkDocument | None:
    try:
        raw = path.read_text(encoding="utf-8")
        if model is TaskDocument:
            return parse_work_document_as(raw, model=TaskDocument, path=path)
        if model is SpecDocument:
            return parse_work_document_as(raw, model=SpecDocument, path=path)
        return parse_work_document_as(raw, model=IncidentDocument, path=path)
    except FileNotFoundError:
        return None
    except (ValidationError, ValueError):
        return None


def _drift_reason(
    document: WorkDocument,
    *,
    target: ClosureTargetState,
    actual_root_spec_id: str | None,
    aliases: set[str],
) -> LineageDiagnosticReason | None:
    if document.root_idea_id == target.root_idea_id:
        return "same_root_idea_different_root_spec"
    if actual_root_spec_id is not None and actual_root_spec_id in aliases:
        return "known_root_spec_alias"
    if _document_spec_id(document) in aliases:
        return "known_root_spec_alias"
    return None


def _task_repair_changes(
    finding: LineageDriftFinding,
    *,
    target: ClosureTargetState,
    aliases: set[str],
) -> tuple[LineageRepairChange, ...]:
    changes = [
        LineageRepairChange(
            work_item_kind=finding.work_item_kind,
            work_item_id=finding.work_item_id,
            state=finding.state,
            path=finding.path,
            field_name="root_spec_id",
            old_value=finding.actual_root_spec_id,
            new_value=target.root_spec_id,
        )
    ]
    if finding.spec_id is not None and finding.spec_id in {finding.actual_root_spec_id, *aliases}:
        changes.append(
            LineageRepairChange(
                work_item_kind=finding.work_item_kind,
                work_item_id=finding.work_item_id,
                state=finding.state,
                path=finding.path,
                field_name="spec_id",
                old_value=finding.spec_id,
                new_value=target.root_spec_id,
            )
        )
    return tuple(changes)


def _incident_repair_changes(
    finding: LineageDriftFinding,
    *,
    target: ClosureTargetState,
) -> tuple[LineageRepairChange, ...]:
    return (
        LineageRepairChange(
            work_item_kind=finding.work_item_kind,
            work_item_id=finding.work_item_id,
            state=finding.state,
            path=finding.path,
            field_name="root_spec_id",
            old_value=finding.actual_root_spec_id,
            new_value=target.root_spec_id,
        ),
    )


def _updates_from_changes(changes: list[LineageRepairChange]) -> dict[str, str]:
    return {change.field_name: change.new_value for change in changes}


def _document_id(document: WorkDocument) -> str:
    if isinstance(document, TaskDocument):
        return document.task_id
    if isinstance(document, IncidentDocument):
        return document.incident_id
    return document.spec_id


def _document_spec_id(document: WorkDocument) -> str | None:
    if isinstance(document, TaskDocument):
        return document.spec_id
    if isinstance(document, IncidentDocument):
        return document.source_spec_id
    return document.spec_id


def _workspace_relative_path(paths: WorkspacePaths, path: Path) -> str:
    try:
        return str(path.relative_to(paths.root))
    except ValueError:
        return str(path)


def _list_markdown_files(directory: Path) -> list[Path]:
    return sorted(path for path in directory.glob("*.md") if path.is_file())


def _coerce_detected_at(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _atomic_write_text(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.tmp-{uuid4().hex}")
    try:
        with temp_path.open("w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


__all__ = [
    "LineageDriftDiagnostic",
    "LineageDriftFinding",
    "LineageRepairChange",
    "LineageRepairPlan",
    "apply_lineage_repair_plan",
    "build_lineage_repair_plan",
    "effective_root_spec_id",
    "known_root_spec_aliases",
    "lineage_drift_diagnostic_path",
    "scan_closure_lineage_drift",
    "write_lineage_drift_diagnostic",
    "write_lineage_repair_report",
]
