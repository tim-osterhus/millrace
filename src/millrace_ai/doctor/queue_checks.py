"""Doctor checks for queue artifact parseability and identity."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, TypeAlias

from pydantic import ValidationError

from millrace_ai.architecture import (
    CompiledRunPlan,
    WorkItemDocumentAdapterDefinition,
    WorkItemFamilyDefinition,
)
from millrace_ai.assets import load_builtin_workflow_primitives
from millrace_ai.contracts import (
    BlueprintDraftDocument,
    IncidentDocument,
    LearningRequestDocument,
    ProbeDocument,
    SpecDocument,
    TaskDocument,
)
from millrace_ai.errors import WorkspaceStateError
from millrace_ai.paths import WorkspacePaths
from millrace_ai.work_documents import read_work_document_as

from .models import DoctorIssue

if TYPE_CHECKING:
    from .checks import DoctorContext

DoctorModel: TypeAlias = (
    type[TaskDocument]
    | type[SpecDocument]
    | type[ProbeDocument]
    | type[IncidentDocument]
    | type[LearningRequestDocument]
    | type[BlueprintDraftDocument]
)
WorkDocument: TypeAlias = (
    TaskDocument
    | SpecDocument
    | ProbeDocument
    | IncidentDocument
    | LearningRequestDocument
    | BlueprintDraftDocument
)


def check_queue_parseability(context: DoctorContext) -> None:
    adapters_by_id = _document_adapters_by_id(context.compiled_plan)
    for family in _work_item_families(context.compiled_plan):
        adapter = adapters_by_id.get(family.document_adapter_id)
        for directory in _family_state_dirs(context.paths, family):
            for path in sorted(
                directory.glob(f"*{family.file_extension}"),
                key=lambda item: item.name,
            ):
                if not path.is_file():
                    continue
                try:
                    model = _known_document_model_for_family(family)
                    document = _read_queue_document(
                        path=path,
                        model=model,
                        family=family,
                        adapter=adapter,
                    )
                    document_id = _work_document_id(document, family=family, path=path)
                    if path.stem != document_id:
                        id_field = family.id_field or f"{family.document_kind}_id"
                        raise WorkspaceStateError(
                            "filename stem does not match "
                            f"{id_field}: expected {document_id}, found {path.stem}"
                        )
                except (OSError, WorkspaceStateError, ValidationError, ValueError) as exc:
                    context.errors.append(
                        DoctorIssue(
                            code="queue_artifact_invalid",
                            message=(
                                f"{family.family_id} via {family.document_adapter_id}: {exc}"
                            ),
                            path=path,
                        )
                    )


def _work_item_families(
    compiled_plan: CompiledRunPlan | None,
) -> tuple[WorkItemFamilyDefinition, ...]:
    if compiled_plan is not None and compiled_plan.work_item_families_by_id:
        return tuple(compiled_plan.work_item_families_by_id.values())
    return load_builtin_workflow_primitives().work_item_families


def _document_adapters_by_id(
    compiled_plan: CompiledRunPlan | None,
) -> dict[str, WorkItemDocumentAdapterDefinition]:
    if compiled_plan is not None and compiled_plan.document_adapters_by_id:
        return dict(compiled_plan.document_adapters_by_id)
    return {
        adapter.adapter_id: adapter
        for adapter in load_builtin_workflow_primitives().document_adapters
    }


def _family_state_dirs(
    paths: WorkspacePaths,
    family: WorkItemFamilyDefinition,
) -> tuple[Path, ...]:
    directories: list[Path] = []
    for dir_key in ("queue", "active", "done", "blocked", "canceled", "superseded"):
        relative = getattr(family.queue_dirs, dir_key)
        if relative is None:
            continue
        directories.append(paths.runtime_root / relative)
    return tuple(directories)


def _known_document_model_for_family(family: WorkItemFamilyDefinition) -> DoctorModel | None:
    models_by_schema_id: dict[str, DoctorModel] = {
        "task_document_v1": TaskDocument,
        "spec_document_v1": SpecDocument,
        "probe_document_v1": ProbeDocument,
        "incident_document_v1": IncidentDocument,
        "learning_request_document_v1": LearningRequestDocument,
        "blueprint_draft_document_v1": BlueprintDraftDocument,
    }
    return models_by_schema_id.get(family.schema_id)


def _read_queue_document(
    *,
    path: Path,
    model: DoctorModel | None,
    family: WorkItemFamilyDefinition,
    adapter: WorkItemDocumentAdapterDefinition | None,
) -> WorkDocument | dict[str, object]:
    _validate_declared_adapter_accepts_path(path=path, family=family, adapter=adapter)
    if family.document_adapter_id == "builtin_markdown_v1":
        if model is None:
            return _read_generic_markdown_queue_document(path)
        return _read_known_work_document(path, model)
    if family.document_adapter_id == "blueprint_draft_markdown_v1":
        if model is not BlueprintDraftDocument:
            raise WorkspaceStateError("blueprint_draft adapter requires BlueprintDraftDocument")
        return BlueprintDraftDocument.model_validate_json(path.read_text(encoding="utf-8"))
    if path.suffix == ".json":
        if model is not None:
            return model.model_validate_json(path.read_text(encoding="utf-8"))
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise WorkspaceStateError("generic JSON queue artifact must be an object")
        return payload
    if model is None:
        return _read_generic_markdown_queue_document(path)
    return _read_known_work_document(path, model)


def _read_known_work_document(path: Path, model: DoctorModel) -> WorkDocument:
    if model is TaskDocument:
        return read_work_document_as(path, model=TaskDocument)
    if model is SpecDocument:
        return read_work_document_as(path, model=SpecDocument)
    if model is ProbeDocument:
        return read_work_document_as(path, model=ProbeDocument)
    if model is IncidentDocument:
        return read_work_document_as(path, model=IncidentDocument)
    if model is LearningRequestDocument:
        return read_work_document_as(path, model=LearningRequestDocument)
    if model is BlueprintDraftDocument:
        return BlueprintDraftDocument.model_validate_json(path.read_text(encoding="utf-8"))
    raise WorkspaceStateError(f"unsupported work document model: {model}")


def _validate_declared_adapter_accepts_path(
    *,
    path: Path,
    family: WorkItemFamilyDefinition,
    adapter: WorkItemDocumentAdapterDefinition | None,
) -> None:
    if adapter is None:
        raise WorkspaceStateError(
            f"family {family.family_id!r} references unknown document adapter "
            f"{family.document_adapter_id!r}"
        )
    if not adapter.can_parse:
        raise WorkspaceStateError(
            f"document adapter {adapter.adapter_id!r} for family {family.family_id!r} "
            "does not declare parse support"
        )
    if path.suffix not in adapter.supported_file_extensions:
        supported = ",".join(adapter.supported_file_extensions)
        raise WorkspaceStateError(
            f"document adapter {adapter.adapter_id!r} does not support extension "
            f"{path.suffix!r}; supported={supported}"
        )


def _read_generic_markdown_queue_document(path: Path) -> dict[str, object]:
    raw = path.read_text(encoding="utf-8")
    if not raw.strip():
        raise WorkspaceStateError("generic markdown queue artifact is empty")
    fields: dict[str, object] = {}
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue
        label, raw_value = stripped.split(":", 1)
        field_name = _generic_markdown_field_name(label)
        value = raw_value.strip()
        if field_name and value:
            fields[field_name] = value
    return fields


def _generic_markdown_field_name(label: str) -> str:
    normalized = label.strip().lower().replace("-", "_").replace(" ", "_")
    return "".join(char for char in normalized if char.isalnum() or char == "_")


def _work_document_id(
    document: WorkDocument | dict[str, object],
    *,
    family: WorkItemFamilyDefinition,
    path: Path,
) -> str:
    if family.id_field is not None:
        value = (
            document.get(family.id_field)
            if isinstance(document, dict)
            else getattr(document, family.id_field, None)
        )
        if isinstance(value, str):
            return value
        raise WorkspaceStateError(
            f"queue artifact is missing string id field {family.id_field!r}"
        )
    if isinstance(document, dict):
        return path.stem
    if isinstance(document, TaskDocument):
        return document.task_id
    if isinstance(document, SpecDocument):
        return document.spec_id
    if isinstance(document, ProbeDocument):
        return document.probe_id
    if isinstance(document, IncidentDocument):
        return document.incident_id
    if isinstance(document, LearningRequestDocument):
        return document.learning_request_id
    return document.draft_id


__all__ = ["check_queue_parseability"]
