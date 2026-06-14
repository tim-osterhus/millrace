"""Blocked metadata persistence and lineage lookup helpers."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import ValidationError, model_validator

from millrace_ai.architecture import WorkItemFamilyDefinition
from millrace_ai.assets import load_builtin_workflow_primitives
from millrace_ai.compilation.persistence import load_existing_plan
from millrace_ai.contracts import (
    StageResultEnvelope,
    TaskDocument,
    WorkItemKind,
)
from millrace_ai.contracts.base import ContractModel
from millrace_ai.contracts.work_refs import family_id_for_work_item_kind, normalize_work_item_family_id
from millrace_ai.events import write_runtime_event
from millrace_ai.workspace.lineage_integrity import effective_root_spec_id
from millrace_ai.workspace.paths import WorkspacePaths
from millrace_ai.workspace.work_documents import parse_work_document_as

from .environmental import (
    BlockedOrigin,
    FailureScope,
    blocked_origin_from_metadata,
    failure_scope_from_metadata,
)
from .retry_policy import AUTO_REQUEUE_FAILURE_CLASSES

if TYPE_CHECKING:
    from millrace_ai.contracts.router import RouterDecision
    from millrace_ai.workspace.family_adapters import WorkFamilyQueueAdapter

from millrace_ai.workspace.family_adapters import (
    queue_adapter_for_family_id as _queue_adapter_for_family_id_default,
)
from millrace_ai.workspace.family_adapters import (
    queue_adapter_for_id as _queue_adapter_for_id_default,
)
from millrace_ai.workspace.family_adapters import (
    resolve_queue_lifecycle_adapter_id as _resolve_queue_lifecycle_adapter_id_default,
)

QUEUE_ADAPTER_FOR_ID = _queue_adapter_for_id_default
QUEUE_ADAPTER_FOR_FAMILY_ID = _queue_adapter_for_family_id_default
RESOLVE_QUEUE_LIFECYCLE_ADAPTER_ID = _resolve_queue_lifecycle_adapter_id_default


class BlockedItemMetadata(ContractModel):
    work_item_family_id: str | None = None
    work_item_kind: WorkItemKind | None = None
    work_item_id: str
    root_spec_id: str | None = None
    root_idea_id: str | None = None
    blocked_at: datetime
    blocked_origin: BlockedOrigin
    failure_class: str
    failure_scope: FailureScope
    auto_requeue_candidate: bool
    source_run_id: str | None = None
    source_plane: str | None = None
    source_stage: str | None = None
    terminal_result: str | None = None
    stage_result_path: str | None = None
    stdout_path: str | None = None
    stderr_path: str | None = None

    @model_validator(mode="after")
    def validate_work_ref(self) -> "BlockedItemMetadata":
        if self.work_item_family_id is None and self.work_item_kind is not None:
            self.work_item_family_id = family_id_for_work_item_kind(self.work_item_kind)
        if self.work_item_family_id is None:
            raise ValueError("work_item_family_id or work_item_kind is required")
        self.work_item_family_id = normalize_work_item_family_id(self.work_item_family_id)
        return self


def blocked_metadata_path(
    paths: WorkspacePaths,
    *,
    kind: WorkItemKind | None = None,
    family_id: str | None = None,
    work_item_id: str,
) -> Path:
    metadata_family_id = normalize_work_item_family_id(
        family_id or family_id_for_work_item_kind(kind) or "",
        field_name="work_item_family_id",
    )
    return paths.runtime_root / "diagnostics" / "blocked" / f"{metadata_family_id}-{work_item_id}.json"


def write_blocked_item_metadata(
    paths: WorkspacePaths,
    *,
    stage_result: StageResultEnvelope,
    decision: "RouterDecision",
    stage_result_path: Path | None = None,
    now: datetime | None = None,
) -> Path:
    metadata = build_blocked_item_metadata(
        paths,
        stage_result=stage_result,
        decision=decision,
        stage_result_path=stage_result_path,
        now=now,
    )
    destination = blocked_metadata_path(
        paths,
        kind=metadata.work_item_kind,
        family_id=metadata.work_item_family_id,
        work_item_id=metadata.work_item_id,
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(metadata.model_dump_json(indent=2) + "\n", encoding="utf-8")
    write_runtime_event(
        paths,
        event_type="blocked_item_metadata_written",
        data={
            "work_item_family_id": metadata.work_item_family_id,
            "work_item_kind": metadata.work_item_kind.value if metadata.work_item_kind is not None else None,
            "work_item_id": metadata.work_item_id,
            "failure_class": metadata.failure_class,
            "failure_scope": metadata.failure_scope,
            "auto_requeue_candidate": metadata.auto_requeue_candidate,
            "metadata_path": _path_relative_to_root(paths, destination),
        },
    )
    return destination


def build_blocked_item_metadata(
    paths: WorkspacePaths,
    *,
    stage_result: StageResultEnvelope,
    decision: "RouterDecision",
    stage_result_path: Path | None = None,
    now: datetime | None = None,
) -> BlockedItemMetadata:
    root_idea_id, root_spec_id = _blocked_work_item_lineage(paths, stage_result)
    failure_class = _metadata_string(stage_result.metadata.get("failure_class"))
    if failure_class is None:
        failure_class = decision.failure_class or "stage_declared_blocked"
    blocked_origin = blocked_origin_from_metadata(stage_result.metadata)
    failure_scope = failure_scope_from_metadata(
        stage_result.metadata,
        blocked_origin=blocked_origin,
    )
    auto_requeue_candidate = (
        bool(stage_result.metadata.get("auto_requeue_candidate"))
        and failure_class in AUTO_REQUEUE_FAILURE_CLASSES
    )
    return BlockedItemMetadata(
        work_item_family_id=stage_result.work_item_family_id,
        work_item_kind=stage_result.work_item_kind,
        work_item_id=stage_result.work_item_id,
        root_spec_id=root_spec_id,
        root_idea_id=root_idea_id,
        blocked_at=now or stage_result.completed_at,
        blocked_origin=blocked_origin,
        failure_class=failure_class,
        failure_scope=failure_scope,
        auto_requeue_candidate=auto_requeue_candidate,
        source_run_id=stage_result.run_id,
        source_plane=stage_result.plane.value,
        source_stage=stage_result.stage.value,
        terminal_result=stage_result.terminal_result.value,
        stage_result_path=_path_relative_to_root(paths, stage_result_path),
        stdout_path=_path_relative_to_root(paths, stage_result.stdout_path),
        stderr_path=_path_relative_to_root(paths, stage_result.stderr_path),
    )


def load_blocked_metadata(
    paths: WorkspacePaths,
    *,
    task_id: str | None = None,
    family_id: str | None = None,
    kind: WorkItemKind | None = None,
    work_item_id: str | None = None,
) -> BlockedItemMetadata | None:
    if task_id is not None:
        kind = WorkItemKind.TASK
        work_item_id = task_id
    if work_item_id is None:
        raise ValueError("work_item_id or task_id is required")
    path = blocked_metadata_path(paths, kind=kind, family_id=family_id, work_item_id=work_item_id)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    try:
        return BlockedItemMetadata.model_validate(payload)
    except ValidationError:
        return None


def _blocked_work_item_lineage(
    paths: WorkspacePaths,
    stage_result: StageResultEnvelope,
) -> tuple[str | None, str | None]:
    if stage_result.work_item_kind is WorkItemKind.TASK:
        return _blocked_task_lineage(paths, stage_result.work_item_id)
    family_id = stage_result.work_item_family_id or (
        stage_result.work_item_kind.value if stage_result.work_item_kind is not None else None
    )
    if family_id is None:
        return None, None
    for path in _candidate_blocked_lineage_paths(
        paths,
        family_id=family_id,
        work_item_id=stage_result.work_item_id,
    ):
        lineage = _lineage_from_artifact(path)
        if lineage != (None, None):
            return lineage
    return None, None


def _blocked_task_lineage(
    paths: WorkspacePaths,
    task_id: str,
) -> tuple[str | None, str | None]:
    path = paths.tasks_blocked_dir / f"{task_id}.md"
    if not path.is_file():
        path = paths.tasks_active_dir / f"{task_id}.md"
    if not path.is_file():
        return None, None
    try:
        task = parse_work_document_as(path.read_text(encoding="utf-8"), model=TaskDocument, path=path)
    except Exception:
        return None, None
    return task.root_idea_id, effective_root_spec_id(task)


def _candidate_blocked_lineage_paths(
    paths: WorkspacePaths,
    *,
    family_id: str,
    work_item_id: str,
) -> tuple[Path, ...]:
    family = _work_item_family_for_lineage(paths, family_id)
    if family is None:
        return ()
    candidates = [
        paths.runtime_root / family.queue_dirs.blocked / f"{work_item_id}{family.file_extension}",
        _lineage_active_path(paths, family=family, work_item_id=work_item_id),
        paths.runtime_root / family.queue_dirs.queue / f"{work_item_id}{family.file_extension}",
    ]
    deduped: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        deduped.append(candidate)
    return tuple(deduped)


def _work_item_family_for_lineage(
    paths: WorkspacePaths,
    family_id: str,
) -> WorkItemFamilyDefinition | None:
    return _work_item_families_by_id(paths).get(family_id)


def _lineage_active_path(
    paths: WorkspacePaths,
    *,
    family: WorkItemFamilyDefinition,
    work_item_id: str,
) -> Path:
    adapter = _queue_adapter_for_family(family)
    if adapter is not None:
        return adapter.active_path(paths, work_item_id=work_item_id)
    return paths.runtime_root / family.queue_dirs.active / f"{work_item_id}{family.file_extension}"


def _lineage_from_artifact(path: Path) -> tuple[str | None, str | None]:
    if not path.is_file():
        return None, None
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None, None
    if path.suffix == ".json":
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return None, None
        if not isinstance(payload, dict):
            return None, None
        return _metadata_string(payload.get("root_idea_id")), _metadata_string(payload.get("root_spec_id"))
    return _lineage_from_markdown(raw)


def _lineage_from_markdown(raw: str) -> tuple[str | None, str | None]:
    root_idea_id: str | None = None
    root_spec_id: str | None = None
    for line in raw.splitlines():
        if line.startswith("Root-Idea-ID:"):
            root_idea_id = line.removeprefix("Root-Idea-ID:").strip() or None
        if line.startswith("Root-Spec-ID:"):
            root_spec_id = line.removeprefix("Root-Spec-ID:").strip() or None
    return root_idea_id, root_spec_id


def _work_item_families_by_id(paths: WorkspacePaths) -> dict[str, WorkItemFamilyDefinition]:
    compiled_plan = load_existing_plan(paths.state_dir / "compiled_plan.json")
    if compiled_plan is not None and compiled_plan.work_item_families_by_id:
        return dict(compiled_plan.work_item_families_by_id)
    return {
        family.family_id: family
        for family in load_builtin_workflow_primitives().work_item_families
    }


def _queue_adapter_for_family(
    family: WorkItemFamilyDefinition,
) -> "WorkFamilyQueueAdapter | None":
    adapter_id = RESOLVE_QUEUE_LIFECYCLE_ADAPTER_ID(family)
    if adapter_id is not None:
        adapter = QUEUE_ADAPTER_FOR_ID(adapter_id)
        if adapter is not None:
            return adapter
    return QUEUE_ADAPTER_FOR_FAMILY_ID(family.family_id)


def _metadata_string(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _path_relative_to_root(paths: WorkspacePaths, path: str | Path | None) -> str | None:
    if path is None:
        return None
    resolved = Path(path)
    try:
        return resolved.resolve().relative_to(paths.root).as_posix()
    except (OSError, ValueError):
        return str(path)


__all__ = [
    "BlockedItemMetadata",
    "blocked_metadata_path",
    "build_blocked_item_metadata",
    "load_blocked_metadata",
    "write_blocked_item_metadata",
]
