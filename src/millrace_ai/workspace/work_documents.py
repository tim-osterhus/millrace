"""Typed markdown work-document parsing and rendering helpers."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from pydantic import ValidationError

from millrace_ai.contracts import IncidentDocument, LearningRequestDocument, SpecDocument, TaskDocument

WorkDocument = TaskDocument | SpecDocument | IncidentDocument | LearningRequestDocument
_DocT = TypeVar("_DocT", TaskDocument, SpecDocument, IncidentDocument, LearningRequestDocument)
_TITLE_PATTERN = re.compile(r"^#\s+(?P<title>.+?)\s*$")
_FIELD_PATTERN = re.compile(r"^(?P<label>[A-Za-z][A-Za-z0-9-]*):(?:\s*(?P<value>.*))?$")
_LIST_ITEM_PATTERN = re.compile(r"^-\s+(?P<value>.+?)\s*$")


@dataclass(frozen=True, slots=True)
class _DocumentSchema:
    model: type[WorkDocument]
    id_field: str
    scalar_fields: tuple[tuple[str, str], ...]
    list_fields: tuple[tuple[str, str], ...]


_TASK_SCHEMA = _DocumentSchema(
    model=TaskDocument,
    id_field="task_id",
    scalar_fields=(
        ("Task-ID", "task_id"),
        ("Title", "title"),
        ("Summary", "summary"),
        ("Root-Idea-ID", "root_idea_id"),
        ("Root-Spec-ID", "root_spec_id"),
        ("Spec-ID", "spec_id"),
        ("Parent-Task-ID", "parent_task_id"),
        ("Incident-ID", "incident_id"),
        ("Status-Hint", "status_hint"),
        ("Created-At", "created_at"),
        ("Created-By", "created_by"),
        ("Updated-At", "updated_at"),
    ),
    list_fields=(
        ("Depends-On", "depends_on"),
        ("Blocks", "blocks"),
        ("Tags", "tags"),
        ("Target-Paths", "target_paths"),
        ("Acceptance", "acceptance"),
        ("Required-Checks", "required_checks"),
        ("References", "references"),
        ("Risk", "risk"),
    ),
)
_SPEC_SCHEMA = _DocumentSchema(
    model=SpecDocument,
    id_field="spec_id",
    scalar_fields=(
        ("Spec-ID", "spec_id"),
        ("Title", "title"),
        ("Summary", "summary"),
        ("Source-Type", "source_type"),
        ("Source-ID", "source_id"),
        ("Parent-Spec-ID", "parent_spec_id"),
        ("Root-Idea-ID", "root_idea_id"),
        ("Root-Spec-ID", "root_spec_id"),
        ("Created-At", "created_at"),
        ("Created-By", "created_by"),
        ("Updated-At", "updated_at"),
    ),
    list_fields=(
        ("Goals", "goals"),
        ("Non-Goals", "non_goals"),
        ("Scope", "scope"),
        ("Constraints", "constraints"),
        ("Assumptions", "assumptions"),
        ("Risks", "risks"),
        ("Target-Paths", "target_paths"),
        ("Entrypoints", "entrypoints"),
        ("Required-Skills", "required_skills"),
        ("Decomposition-Hints", "decomposition_hints"),
        ("Acceptance", "acceptance"),
        ("References", "references"),
    ),
)
_INCIDENT_SCHEMA = _DocumentSchema(
    model=IncidentDocument,
    id_field="incident_id",
    scalar_fields=(
        ("Incident-ID", "incident_id"),
        ("Title", "title"),
        ("Summary", "summary"),
        ("Root-Idea-ID", "root_idea_id"),
        ("Root-Spec-ID", "root_spec_id"),
        ("Source-Task-ID", "source_task_id"),
        ("Source-Spec-ID", "source_spec_id"),
        ("Source-Stage", "source_stage"),
        ("Source-Plane", "source_plane"),
        ("Failure-Class", "failure_class"),
        ("Severity", "severity"),
        ("Needs-Planning", "needs_planning"),
        ("Trigger-Reason", "trigger_reason"),
        ("Consultant-Decision", "consultant_decision"),
        ("Opened-At", "opened_at"),
        ("Opened-By", "opened_by"),
        ("Updated-At", "updated_at"),
    ),
    list_fields=(
        ("Observed-Symptoms", "observed_symptoms"),
        ("Failed-Attempts", "failed_attempts"),
        ("Evidence-Paths", "evidence_paths"),
        ("Related-Run-IDs", "related_run_ids"),
        ("Related-Stage-Results", "related_stage_results"),
        ("References", "references"),
    ),
)
_LEARNING_REQUEST_SCHEMA = _DocumentSchema(
    model=LearningRequestDocument,
    id_field="learning_request_id",
    scalar_fields=(
        ("Learning-Request-ID", "learning_request_id"),
        ("Title", "title"),
        ("Summary", "summary"),
        ("Requested-Action", "requested_action"),
        ("Target-Skill-ID", "target_skill_id"),
        ("Target-Stage", "target_stage"),
        ("Trigger-Metadata", "trigger_metadata"),
        ("Created-At", "created_at"),
        ("Created-By", "created_by"),
        ("Updated-At", "updated_at"),
    ),
    list_fields=(
        ("Source-Refs", "source_refs"),
        ("Preferred-Output-Paths", "preferred_output_paths"),
        ("Originating-Run-IDs", "originating_run_ids"),
        ("Artifact-Paths", "artifact_paths"),
        ("References", "references"),
    ),
)
_SCHEMA_BY_MODEL: dict[type[WorkDocument], _DocumentSchema] = {
    TaskDocument: _TASK_SCHEMA,
    SpecDocument: _SPEC_SCHEMA,
    IncidentDocument: _INCIDENT_SCHEMA,
    LearningRequestDocument: _LEARNING_REQUEST_SCHEMA,
}
_SCHEMA_BY_ID_FIELD: dict[str, _DocumentSchema] = {
    schema.id_field: schema for schema in _SCHEMA_BY_MODEL.values()
}
_FIELD_NAME_BY_LABEL: dict[str, str] = {
    label: field_name
    for schema in _SCHEMA_BY_MODEL.values()
    for label, field_name in schema.scalar_fields + schema.list_fields
}
_FIELD_KIND_BY_LABEL: dict[str, str] = {}
for _schema in _SCHEMA_BY_MODEL.values():
    for _label, _field_name in _schema.scalar_fields:
        _existing = _FIELD_KIND_BY_LABEL.setdefault(_label, "scalar")
        if _existing != "scalar":
            raise RuntimeError(f"field label kind mismatch for {_label}")
    for _label, _field_name in _schema.list_fields:
        _existing = _FIELD_KIND_BY_LABEL.setdefault(_label, "list")
        if _existing != "list":
            raise RuntimeError(f"field label kind mismatch for {_label}")


def parse_work_document(raw: str, *, path: Path | None = None) -> WorkDocument:
    """Parse one human-facing markdown work document."""

    heading_title, field_payload = _parse_markdown_fields(raw, path=path)
    model = _infer_model(field_payload, path=path)
    if model is TaskDocument:
        return _validate_document(model=TaskDocument, heading_title=heading_title, field_payload=field_payload)
    if model is SpecDocument:
        return _validate_document(model=SpecDocument, heading_title=heading_title, field_payload=field_payload)
    if model is IncidentDocument:
        return _validate_document(model=IncidentDocument, heading_title=heading_title, field_payload=field_payload)
    return _validate_document(
        model=LearningRequestDocument,
        heading_title=heading_title,
        field_payload=field_payload,
    )


def parse_work_document_as(raw: str, *, model: type[_DocT], path: Path | None = None) -> _DocT:
    """Parse markdown work document and enforce the expected model type."""

    heading_title, field_payload = _parse_markdown_fields(raw, path=path)
    return _validate_document(model=model, heading_title=heading_title, field_payload=field_payload)


def read_work_document(path: Path) -> WorkDocument:
    """Read and parse one markdown work document."""

    return parse_work_document(path.read_text(encoding="utf-8"), path=path)


def read_work_document_as(path: Path, *, model: type[_DocT]) -> _DocT:
    """Read and parse one markdown work document as an expected model type."""

    return parse_work_document_as(path.read_text(encoding="utf-8"), model=model, path=path)


def render_work_document(document: WorkDocument, *, body: str | None = None) -> str:
    """Render a canonical operator-facing markdown work document."""

    schema = _SCHEMA_BY_MODEL[type(document)]
    payload = document.model_dump(mode="json")
    lines: list[str] = [f"# {document.title}", ""]

    for label, field_name in schema.scalar_fields:
        value = payload.get(field_name)
        if value is None:
            continue
        if field_name == "summary" and value == "":
            continue
        if field_name == "trigger_metadata" and value == {}:
            continue
        if field_name == "severity" and value == "medium":
            continue
        if field_name == "needs_planning" and value is True:
            continue
        lines.append(f"{label}: {_render_scalar(value)}")

    for label, field_name in schema.list_fields:
        values = payload.get(field_name) or []
        if not values:
            continue
        lines.append("")
        lines.append(f"{label}:")
        lines.extend(f"- {item}" for item in values)

    rendered_body = (body or "").strip()
    if rendered_body:
        lines.extend(("", rendered_body))
    return "\n".join(lines).rstrip() + "\n"


def parse_json_import(raw: str, *, model: type[_DocT]) -> _DocT:
    """Parse explicit JSON import payload for a work document model."""

    return model.model_validate_json(raw)


def read_json_import(path: Path, *, model: type[_DocT]) -> _DocT:
    """Read explicit JSON import payload for a work document model."""

    return parse_json_import(path.read_text(encoding="utf-8"), model=model)


def _parse_markdown_fields(raw: str, *, path: Path | None) -> tuple[str, dict[str, object]]:
    lines = raw.splitlines()
    source_name = "<memory>" if path is None else path.name
    if not lines:
        raise ValueError(f"work document {source_name} is empty")

    title_match = _TITLE_PATTERN.match(lines[0].strip())
    if title_match is None:
        raise ValueError(f"work document {source_name} must start with a markdown H1 title")

    heading_title = title_match.group("title").strip()
    if not heading_title:
        raise ValueError(f"work document {source_name} has an empty H1 title")

    payload: dict[str, object] = {}
    index = 1
    while index < len(lines):
        raw_line = lines[index].rstrip()
        stripped = raw_line.strip()
        index += 1
        if not stripped:
            continue

        field_match = _FIELD_PATTERN.match(stripped)
        if field_match is None:
            continue

        label = field_match.group("label")
        field_name = _FIELD_NAME_BY_LABEL.get(label)
        field_kind = _FIELD_KIND_BY_LABEL.get(label)
        inline_value = (field_match.group("value") or "").strip()
        if field_name is None:
            index = _skip_unknown_field_block(lines, index)
            continue
        if field_name in payload:
            raise ValueError(f"work document {source_name} repeats field `{label}`")

        if inline_value:
            payload[field_name] = inline_value
            continue
        if field_kind == "scalar":
            index = _skip_blank_scalar_block(lines, index, label=label, source_name=source_name)
            continue

        items: list[str] = []
        while index < len(lines):
            next_line = lines[index].strip()
            if not next_line:
                index += 1
                if items:
                    break
                continue
            if _FIELD_PATTERN.match(next_line):
                break
            item_match = _LIST_ITEM_PATTERN.match(next_line)
            if item_match is None:
                raise ValueError(f"work document {source_name} has invalid list item under `{label}`")
            items.append(item_match.group("value").strip())
            index += 1
        payload[field_name] = items

    return heading_title, payload


def _skip_blank_scalar_block(lines: list[str], index: int, *, label: str, source_name: str) -> int:
    while index < len(lines):
        candidate = lines[index].strip()
        if not candidate:
            index += 1
            continue
        if _FIELD_PATTERN.match(candidate):
            break
        if _LIST_ITEM_PATTERN.match(candidate):
            raise ValueError(f"work document {source_name} has list item under scalar field `{label}`")
        break
    return index


def _skip_unknown_field_block(lines: list[str], index: int) -> int:
    while index < len(lines):
        candidate = lines[index].strip()
        if not candidate:
            index += 1
            continue
        if _FIELD_PATTERN.match(candidate):
            break
        index += 1
    return index


def _infer_model(field_payload: dict[str, object], *, path: Path | None) -> type[WorkDocument]:
    source_name = "<memory>" if path is None else path.name
    matched_id_fields = sorted(field_name for field_name in _SCHEMA_BY_ID_FIELD if field_name in field_payload)
    if not matched_id_fields:
        raise ValueError(f"work document {source_name} must include one canonical document identifier")
    if len(matched_id_fields) > 1:
        raise ValueError(f"work document {source_name} mixes multiple document identifier fields")
    return _SCHEMA_BY_ID_FIELD[matched_id_fields[0]].model


def _validate_document(
    *,
    model: type[_DocT],
    heading_title: str,
    field_payload: dict[str, object],
) -> _DocT:
    payload = dict(field_payload)
    trigger_metadata = payload.get("trigger_metadata")
    if isinstance(trigger_metadata, str):
        payload["trigger_metadata"] = json.loads(trigger_metadata)
    title_value = payload.get("title")
    if title_value is None:
        payload["title"] = heading_title
    elif str(title_value).strip() != heading_title:
        raise ValueError("markdown H1 title must match the `Title` field")
    return model.model_validate(payload)


def _render_scalar(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, dict):
        return json.dumps(value, sort_keys=True, separators=(",", ":"))
    return str(value)


__all__ = [
    "WorkDocument",
    "parse_work_document",
    "parse_work_document_as",
    "read_work_document",
    "read_work_document_as",
    "render_work_document",
    "parse_json_import",
    "read_json_import",
    "ValidationError",
]
