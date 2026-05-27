"""Blocked queue mutation, family resolution, and auto-recovery orchestration."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeAlias

from pydantic import ValidationError

from millrace_ai.architecture import WorkItemDocumentAdapterDefinition, WorkItemFamilyDefinition
from millrace_ai.assets import load_builtin_workflow_primitives
from millrace_ai.compilation.persistence import load_existing_plan
from millrace_ai.config import RuntimeConfig
from millrace_ai.contracts import (
    BlueprintDraftDocument,
    IncidentDocument,
    LearningRequestDocument,
    Plane,
    ProbeDocument,
    RuntimeSnapshot,
    SpecDocument,
    TaskDocument,
    WorkItemKind,
)
from millrace_ai.contracts.base import ContractModel
from millrace_ai.contracts.work_refs import coerce_family_and_kind, normalize_work_item_family_id
from millrace_ai.errors import QueueStateError
from millrace_ai.events import write_runtime_event
from millrace_ai.queue_store import QueueStore
from millrace_ai.state_store import load_snapshot, save_snapshot
from millrace_ai.workspace.lineage_integrity import effective_root_spec_id
from millrace_ai.workspace.paths import WorkspacePaths
from millrace_ai.workspace.work_documents import parse_work_document_as
from millrace_ai.workspace.work_inventory import queue_depths_by_plane

from .blocked_metadata import BlockedItemMetadata, load_blocked_metadata
from .events import emit_auto_recovery_skipped, emit_blocked_retry_events
from .retry_policy import count_auto_requeues, metadata_allows_auto_requeue

if TYPE_CHECKING:
    from millrace_ai.runtime.engine import RuntimeEngine
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


QueueDocumentModel: TypeAlias = (
    type[TaskDocument]
    | type[SpecDocument]
    | type[ProbeDocument]
    | type[IncidentDocument]
    | type[LearningRequestDocument]
    | type[BlueprintDraftDocument]
)


class BlockedTaskRequeueResult(ContractModel):
    task_id: str
    source_path: str
    destination_path: str
    source_state: str = "blocked"
    destination_state: str = "queue"
    actor: str
    auto: bool
    reason: str
    failure_class: str | None = None
    attempt_number: int
    diagnostics_path: str | None = None


class BlockedWorkItemRetryResult(ContractModel):
    work_item_family_id: str
    work_item_kind: WorkItemKind | None = None
    work_item_id: str
    source_path: str
    destination_path: str
    source_state: str = "blocked"
    destination_state: str = "queue"
    actor: str
    auto: bool
    reason: str
    failure_class: str | None = None
    attempt_number: int
    diagnostics_path: str | None = None


class StrandedBlockedDependency(ContractModel):
    blocked_task_id: str
    queued_dependent_ids: tuple[str, ...]
    root_spec_id: str | None = None
    metadata: BlockedItemMetadata | None = None


@dataclass(frozen=True, slots=True)
class _BlockedRetryFamily:
    family_id: str
    work_item_kind: WorkItemKind | None
    queue_dir: Path
    blocked_dir: Path
    active_dir: Path | None
    done_dir: Path | None
    file_extension: str
    id_field: str | None
    lineage_fields: tuple[str, ...]
    queue_adapter: WorkFamilyQueueAdapter | None = None
    family_definition: WorkItemFamilyDefinition | None = None
    document_adapter: WorkItemDocumentAdapterDefinition | None = None


_DOCUMENT_MODEL_BY_SCHEMA_ID: dict[str, QueueDocumentModel] = {
    "task_document_v1": TaskDocument,
    "spec_document_v1": SpecDocument,
    "probe_document_v1": ProbeDocument,
    "incident_document_v1": IncidentDocument,
    "learning_request_document_v1": LearningRequestDocument,
    "_".join(("blueprint", "draft", "document", "v1")): BlueprintDraftDocument,
}


def retry_blocked_work_item(
    paths: WorkspacePaths,
    *,
    work_item_id: str,
    reason: str,
    actor: str,
    auto: bool,
    work_item_family_id: str | None = None,
    work_item_kind: WorkItemKind | None = None,
    force: bool = False,
    root_spec_id: str | None = None,
    config: RuntimeConfig | None = None,
    diagnostics_path: Path | None = None,
) -> BlockedWorkItemRetryResult:
    cleaned_reason = reason.strip()
    if not cleaned_reason:
        raise QueueStateError("requeue reason is required")

    family = _resolve_blocked_retry_family(
        paths,
        work_item_id=work_item_id,
        work_item_family_id=work_item_family_id,
        work_item_kind=work_item_kind,
    )
    _validate_blocked_work_item_locations(paths, family, work_item_id)
    document = _parse_blocked_retry_document(family, work_item_id)
    _validate_retry_root_spec_guard(family, document, work_item_id=work_item_id, root_spec_id=root_spec_id)

    metadata = load_blocked_metadata(
        paths,
        family_id=family.family_id,
        kind=family.work_item_kind,
        work_item_id=work_item_id,
    )
    retryable = metadata_allows_auto_requeue(metadata)
    if not force and not retryable:
        label = "blocked task" if family.family_id == WorkItemKind.TASK.value else "blocked work item"
        raise QueueStateError(f"{label} is not retryable; rerun with --force to override")

    auto_attempts = count_auto_requeues(paths, queue_dir=family.queue_dir, work_item_id=work_item_id)
    max_attempts = config.auto_recovery.max_auto_requeues_per_work_item if config is not None else 3
    if not force and auto_attempts >= max_attempts:
        label = "blocked task" if family.family_id == WorkItemKind.TASK.value else "blocked work item"
        raise QueueStateError(f"{label} retry budget is exhausted")

    attempt_number = auto_attempts + 1
    failure_class = metadata.failure_class if metadata is not None else None
    source_path = family.blocked_dir / f"{work_item_id}{family.file_extension}"
    destination_path = family.queue_dir / f"{work_item_id}{family.file_extension}"
    QueueStore(paths).requeue_blocked_work_item(
        work_item_family_id=family.family_id,
        work_item_kind=family.work_item_kind,
        work_item_id=work_item_id,
        reason=cleaned_reason,
        actor=actor,
        auto=auto,
        failure_class=failure_class,
        attempt_number=attempt_number,
        blocked_dir=family.blocked_dir,
        queue_dir=family.queue_dir,
        file_extension=family.file_extension,
    )
    _refresh_snapshot_queue_depths(paths)
    result = BlockedWorkItemRetryResult(
        work_item_family_id=family.family_id,
        work_item_kind=family.work_item_kind,
        work_item_id=work_item_id,
        source_path=str(source_path),
        destination_path=str(destination_path),
        actor=actor,
        auto=auto,
        reason=cleaned_reason,
        failure_class=failure_class,
        attempt_number=attempt_number,
        diagnostics_path=str(diagnostics_path) if diagnostics_path is not None else None,
    )
    emit_blocked_retry_events(paths, result)
    return result


def retry_blocked_task(
    paths: WorkspacePaths,
    *,
    task_id: str,
    reason: str,
    actor: str,
    auto: bool,
    force: bool = False,
    root_spec_id: str | None = None,
    config: RuntimeConfig | None = None,
    diagnostics_path: Path | None = None,
) -> BlockedTaskRequeueResult:
    result = retry_blocked_work_item(
        paths,
        work_item_family_id=WorkItemKind.TASK.value,
        work_item_kind=WorkItemKind.TASK,
        work_item_id=task_id,
        reason=reason,
        actor=actor,
        auto=auto,
        force=force,
        root_spec_id=root_spec_id,
        config=config,
        diagnostics_path=diagnostics_path,
    )
    return BlockedTaskRequeueResult(
        task_id=task_id,
        source_path=result.source_path,
        destination_path=result.destination_path,
        actor=actor,
        auto=auto,
        reason=result.reason,
        failure_class=result.failure_class,
        attempt_number=result.attempt_number,
        diagnostics_path=str(diagnostics_path) if diagnostics_path is not None else None,
    )


def attempt_stranded_dependency_auto_recovery(
    engine: RuntimeEngine,
) -> BlockedTaskRequeueResult | None:
    assert engine.snapshot is not None
    assert engine.config is not None
    policy = engine.config.auto_recovery
    snapshot = engine.snapshot
    if not policy.enabled or not policy.blocked_dependency_retry_enabled:
        return None
    if snapshot.paused or snapshot.stop_requested or snapshot.active_runs_by_plane:
        return None
    if snapshot.queue_depth_execution <= 0 and engine._execution_queue_depth() <= 0:
        return None

    candidate = _find_stranded_blocked_dependency(engine.paths)
    if candidate is None:
        return None
    metadata = candidate.metadata
    if not metadata_allows_auto_requeue(metadata):
        emit_auto_recovery_skipped(
            engine,
            candidate,
            reason="blocked_dependency_not_retryable",
        )
        return None
    assert metadata is not None

    auto_attempts = count_auto_requeues(engine.paths, task_id=candidate.blocked_task_id)
    if auto_attempts >= policy.max_auto_requeues_per_work_item:
        emit_auto_recovery_skipped(engine, candidate, reason="retry_budget_exhausted")
        return None
    now = engine._now()
    cooldown = policy.cooldown_seconds[min(auto_attempts, len(policy.cooldown_seconds) - 1)]
    elapsed = (now - metadata.blocked_at).total_seconds()
    if elapsed < cooldown:
        emit_auto_recovery_skipped(engine, candidate, reason="cooldown_active")
        return None

    diagnostics_path = _write_auto_recovery_diagnostics(
        engine.paths,
        candidate=candidate,
        snapshot=snapshot,
        now=now,
        decision="requeue",
        reason="transient blocked dependency",
        auto_attempt_number=auto_attempts + 1,
    )
    result = retry_blocked_task(
        engine.paths,
        task_id=candidate.blocked_task_id,
        reason="transient blocked dependency auto-recovery",
        actor="runtime-daemon",
        auto=True,
        force=False,
        root_spec_id=candidate.root_spec_id,
        config=engine.config,
        diagnostics_path=diagnostics_path,
    )
    engine._refresh_runtime_queue_depths(process_running=True)
    write_runtime_event(
        engine.paths,
        event_type="blocked_dependency_auto_requeued",
        data={
            "task_id": candidate.blocked_task_id,
            "queued_dependents": list(candidate.queued_dependent_ids),
            "failure_class": metadata.failure_class,
            "diagnostics_path": _path_relative_to_root(engine.paths, diagnostics_path),
        },
    )
    engine._emit_monitor_event(
        "blocked_dependency_auto_requeued",
        task_id=candidate.blocked_task_id,
        queued_dependents=list(candidate.queued_dependent_ids),
        failure_class=metadata.failure_class,
    )
    return result


def _find_stranded_blocked_dependency(
    paths: WorkspacePaths,
) -> StrandedBlockedDependency | None:
    completed = {path.stem for path in paths.tasks_done_dir.glob("*.md") if path.is_file()}
    dependents_by_blocked_id: dict[str, list[str]] = {}
    root_by_blocked_id: dict[str, str | None] = {}

    for queued_path in sorted(paths.tasks_queue_dir.glob("*.md")):
        try:
            queued = parse_work_document_as(
                queued_path.read_text(encoding="utf-8"),
                model=TaskDocument,
                path=queued_path,
            )
        except (OSError, ValidationError, ValueError):
            continue
        missing_dependencies = tuple(
            dependency for dependency in queued.depends_on if dependency not in completed
        )
        if not missing_dependencies:
            continue
        queued_root = effective_root_spec_id(queued)
        for dependency in missing_dependencies:
            blocked_path = paths.tasks_blocked_dir / f"{dependency}.md"
            if not blocked_path.is_file():
                continue
            try:
                blocked = parse_work_document_as(
                    blocked_path.read_text(encoding="utf-8"),
                    model=TaskDocument,
                    path=blocked_path,
                )
            except (OSError, ValidationError, ValueError):
                continue
            blocked_root = effective_root_spec_id(blocked)
            if queued_root is not None and blocked_root is not None and queued_root != blocked_root:
                continue
            dependents_by_blocked_id.setdefault(dependency, []).append(queued.task_id)
            root_by_blocked_id[dependency] = blocked_root or queued_root

    for blocked_task_id in sorted(dependents_by_blocked_id):
        metadata = load_blocked_metadata(paths, task_id=blocked_task_id)
        return StrandedBlockedDependency(
            blocked_task_id=blocked_task_id,
            queued_dependent_ids=tuple(sorted(dependents_by_blocked_id[blocked_task_id])),
            root_spec_id=root_by_blocked_id.get(blocked_task_id),
            metadata=metadata,
        )
    return None


def _resolve_blocked_retry_family(
    paths: WorkspacePaths,
    *,
    work_item_id: str,
    work_item_family_id: str | None,
    work_item_kind: WorkItemKind | None,
) -> _BlockedRetryFamily:
    if work_item_family_id is not None or work_item_kind is not None:
        family_id, kind = coerce_family_and_kind(
            family_id=work_item_family_id,
            work_item_kind=work_item_kind,
        )
        if family_id is None:
            raise QueueStateError("blocked retry requires work_item_family_id or work_item_kind")
        return _blocked_retry_family_for_id(paths, family_id=family_id, work_item_kind=kind)

    candidates = [
        family
        for family in _all_blocked_retry_families(paths)
        if (family.blocked_dir / f"{work_item_id}{family.file_extension}").is_file()
    ]
    if not candidates:
        raise QueueStateError(f"blocked work item not found: {work_item_id}")
    if len(candidates) > 1:
        families = ", ".join(sorted(family.family_id for family in candidates))
        raise QueueStateError(
            f"blocked work item id is ambiguous; pass --family for {work_item_id}: {families}"
        )
    return candidates[0]


def _all_blocked_retry_families(paths: WorkspacePaths) -> tuple[_BlockedRetryFamily, ...]:
    families_by_id = _work_item_families_by_id(paths)
    adapters_by_id = _document_adapters_by_id(paths)
    resolved: list[_BlockedRetryFamily] = []
    for family_id in sorted(families_by_id):
        try:
            resolved.append(
                _blocked_retry_family_from_definition(
                    paths,
                    family=families_by_id[family_id],
                    document_adapter=adapters_by_id.get(families_by_id[family_id].document_adapter_id),
                )
            )
        except QueueStateError:
            continue
    return tuple(resolved)


def _blocked_retry_family_for_id(
    paths: WorkspacePaths,
    *,
    family_id: str,
    work_item_kind: WorkItemKind | None,
) -> _BlockedRetryFamily:
    normalized_family_id = normalize_work_item_family_id(family_id)
    family = _work_item_families_by_id(paths).get(normalized_family_id)
    if family is None:
        raise QueueStateError(f"blocked retry for family {normalized_family_id} is not supported")
    expected_kind = _family_work_item_kind(normalized_family_id)
    if work_item_kind is not None and expected_kind is not None and work_item_kind is not expected_kind:
        raise QueueStateError("work_item_family_id must agree with work_item_kind")
    document_adapter = _document_adapters_by_id(paths).get(family.document_adapter_id)
    return _blocked_retry_family_from_definition(
        paths,
        family=family,
        document_adapter=document_adapter,
        work_item_kind=work_item_kind,
    )


def _blocked_retry_family_from_definition(
    paths: WorkspacePaths,
    *,
    family: WorkItemFamilyDefinition,
    document_adapter: WorkItemDocumentAdapterDefinition | None,
    work_item_kind: WorkItemKind | None = None,
) -> _BlockedRetryFamily:
    if "retry" not in family.operator_capabilities:
        raise QueueStateError(f"blocked retry for family {family.family_id} is not supported")
    if document_adapter is None:
        raise QueueStateError(
            f"blocked retry for family {family.family_id} references unknown document adapter "
            f"{family.document_adapter_id}"
        )
    if not document_adapter.can_parse:
        raise QueueStateError(f"blocked retry for family {family.family_id} has no parser")
    if family.file_extension not in document_adapter.supported_file_extensions:
        supported = ",".join(document_adapter.supported_file_extensions)
        raise QueueStateError(
            f"blocked retry for family {family.family_id} does not support extension "
            f"{family.file_extension}; supported={supported}"
        )
    resolved_kind = work_item_kind or _family_work_item_kind(family.family_id)
    return _BlockedRetryFamily(
        family_id=family.family_id,
        work_item_kind=resolved_kind,
        queue_dir=paths.runtime_root / family.queue_dirs.queue,
        blocked_dir=paths.runtime_root / family.queue_dirs.blocked,
        active_dir=paths.runtime_root / family.queue_dirs.active,
        done_dir=paths.runtime_root / family.queue_dirs.done,
        file_extension=family.file_extension,
        id_field=family.id_field,
        lineage_fields=family.lineage_fields,
        queue_adapter=_queue_adapter_for_family(family),
        family_definition=family,
        document_adapter=document_adapter,
    )


def _validate_blocked_work_item_locations(
    paths: WorkspacePaths,
    family: _BlockedRetryFamily,
    work_item_id: str,
) -> None:
    blocked_path = family.blocked_dir / f"{work_item_id}{family.file_extension}"
    if not blocked_path.is_file():
        raise QueueStateError(f"{family.family_id} {work_item_id} is not blocked")
    queue_path = family.queue_dir / f"{work_item_id}{family.file_extension}"
    if queue_path.exists():
        raise QueueStateError(f"{family.family_id} {work_item_id} is already queue")
    active_path = _blocked_retry_active_path(paths, family=family, work_item_id=work_item_id)
    if active_path is not None and active_path.exists():
        raise QueueStateError(f"{family.family_id} {work_item_id} is already active")
    if family.done_dir is not None and (family.done_dir / f"{work_item_id}{family.file_extension}").exists():
        raise QueueStateError(f"{family.family_id} {work_item_id} is already done")


def _blocked_retry_active_path(
    paths: WorkspacePaths,
    *,
    family: _BlockedRetryFamily,
    work_item_id: str,
) -> Path | None:
    if family.queue_adapter is not None:
        return family.queue_adapter.active_path(paths, work_item_id=work_item_id)
    if family.active_dir is None:
        return None
    return family.active_dir / f"{work_item_id}{family.file_extension}"


def _parse_blocked_retry_document(family: _BlockedRetryFamily, work_item_id: str) -> Any:
    path = family.blocked_dir / f"{work_item_id}{family.file_extension}"
    try:
        raw = path.read_text(encoding="utf-8")
        document = _parse_family_defined_document(family, raw, path=path)
        document_id = _document_work_item_id(document, family=family, fallback=path.stem)
        if document_id != path.stem:
            id_field = family.id_field or f"{family.family_id}_id"
            raise QueueStateError(
                f"filename stem does not match {id_field}: expected {document_id}, found {path.stem}"
            )
        return document
    except QueueStateError:
        raise
    except (OSError, ValidationError, ValueError, json.JSONDecodeError) as exc:
        raise QueueStateError(f"blocked {family.family_id} {work_item_id} is invalid: {exc}") from exc


def _parse_family_defined_document(family: _BlockedRetryFamily, raw: str, *, path: Path) -> Any:
    assert family.family_definition is not None
    model = _DOCUMENT_MODEL_BY_SCHEMA_ID.get(family.family_definition.schema_id)
    if model is not None:
        return _parse_known_queue_document(raw, model=model, path=path)
    if path.suffix == ".json":
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise QueueStateError("generic JSON queue artifact must be an object")
        return payload
    return _generic_markdown_fields(raw)


def _generic_markdown_fields(raw: str) -> dict[str, object]:
    if not raw.strip():
        raise QueueStateError("generic markdown queue artifact is empty")
    fields: dict[str, object] = {}
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue
        label, raw_value = stripped.split(":", 1)
        field_name = label.strip().lower().replace("-", "_").replace(" ", "_")
        field_name = "".join(char for char in field_name if char.isalnum() or char == "_")
        value = raw_value.strip()
        if field_name and value:
            fields[field_name] = value
    return fields


def _parse_known_queue_document(
    raw: str,
    *,
    model: QueueDocumentModel,
    path: Path,
) -> Any:
    if model is TaskDocument:
        return parse_work_document_as(raw, model=TaskDocument, path=path)
    if model is SpecDocument:
        return parse_work_document_as(raw, model=SpecDocument, path=path)
    if model is IncidentDocument:
        return parse_work_document_as(raw, model=IncidentDocument, path=path)
    if model is ProbeDocument:
        return parse_work_document_as(raw, model=ProbeDocument, path=path)
    if model is LearningRequestDocument:
        return parse_work_document_as(raw, model=LearningRequestDocument, path=path)
    if model is BlueprintDraftDocument:
        return BlueprintDraftDocument.model_validate_json(raw)
    raise QueueStateError(f"unsupported queue document model: {model}")


def _validate_retry_root_spec_guard(
    family: _BlockedRetryFamily,
    document: Any,
    *,
    work_item_id: str,
    root_spec_id: str | None,
) -> None:
    if root_spec_id is None:
        return
    document_root_spec_id = _document_root_spec_id(document)
    exposes_root_spec_id = document_root_spec_id is not None or "root_spec_id" in family.lineage_fields
    if not exposes_root_spec_id:
        return
    if document_root_spec_id != root_spec_id:
        raise QueueStateError(
            f"blocked {family.family_id} {work_item_id} does not belong to root spec {root_spec_id}"
        )


def _document_root_spec_id(document: Any) -> str | None:
    if isinstance(document, TaskDocument):
        return effective_root_spec_id(document)
    if isinstance(document, dict):
        value = document.get("root_spec_id")
        return value.strip() if isinstance(value, str) and value.strip() else None
    value = getattr(document, "root_spec_id", None)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _document_work_item_id(document: Any, *, family: _BlockedRetryFamily, fallback: str) -> str:
    if family.id_field is not None:
        if isinstance(document, dict):
            value = document.get(family.id_field)
        else:
            value = getattr(document, family.id_field, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return fallback


def _work_item_families_by_id(paths: WorkspacePaths) -> dict[str, WorkItemFamilyDefinition]:
    compiled_plan = load_existing_plan(paths.state_dir / "compiled_plan.json")
    if compiled_plan is not None and compiled_plan.work_item_families_by_id:
        return dict(compiled_plan.work_item_families_by_id)
    return {
        family.family_id: family
        for family in load_builtin_workflow_primitives().work_item_families
    }


def _document_adapters_by_id(paths: WorkspacePaths) -> dict[str, WorkItemDocumentAdapterDefinition]:
    compiled_plan = load_existing_plan(paths.state_dir / "compiled_plan.json")
    if compiled_plan is not None and compiled_plan.document_adapters_by_id:
        return dict(compiled_plan.document_adapters_by_id)
    return {
        adapter.adapter_id: adapter
        for adapter in load_builtin_workflow_primitives().document_adapters
    }


def _family_work_item_kind(family_id: str) -> WorkItemKind | None:
    try:
        return WorkItemKind(family_id)
    except ValueError:
        return None


def _queue_adapter_for_family(
    family: WorkItemFamilyDefinition,
) -> WorkFamilyQueueAdapter | None:
    adapter_id = RESOLVE_QUEUE_LIFECYCLE_ADAPTER_ID(family)
    if adapter_id is not None:
        adapter = QUEUE_ADAPTER_FOR_ID(adapter_id)
        if adapter is None:
            raise QueueStateError(
                f"work item family {family.family_id} references unknown adapter {adapter_id}"
            )
        return adapter
    return QUEUE_ADAPTER_FOR_FAMILY_ID(family.family_id)


def _refresh_snapshot_queue_depths(paths: WorkspacePaths) -> None:
    if not paths.runtime_snapshot_file.is_file():
        return
    try:
        snapshot = load_snapshot(paths)
    except Exception:
        return
    compiled_plan = load_existing_plan(paths.state_dir / "compiled_plan.json")
    queue_depths = queue_depths_by_plane(paths, compiled_plan=compiled_plan)
    updated = snapshot.model_copy(
        update={
            "queue_depth_execution": queue_depths[Plane.EXECUTION],
            "queue_depth_planning": queue_depths[Plane.PLANNING],
            "queue_depth_learning": queue_depths[Plane.LEARNING],
            "queue_depths_by_plane": queue_depths,
            "updated_at": datetime.now(timezone.utc),
        }
    )
    save_snapshot(paths, updated)


def _write_auto_recovery_diagnostics(
    paths: WorkspacePaths,
    *,
    candidate: StrandedBlockedDependency,
    snapshot: RuntimeSnapshot,
    now: datetime,
    decision: str,
    reason: str,
    auto_attempt_number: int,
) -> Path:
    destination = (
        paths.runtime_root
        / "diagnostics"
        / "auto-recovery"
        / f"{now.strftime('%Y%m%dT%H%M%SZ')}-{candidate.blocked_task_id}.json"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "1.0",
        "kind": "blocked_dependency_auto_recovery",
        "decision": decision,
        "reason": reason,
        "created_at": now.isoformat(),
        "blocked_task_id": candidate.blocked_task_id,
        "queued_dependent_ids": list(candidate.queued_dependent_ids),
        "root_spec_id": candidate.root_spec_id,
        "auto_attempt_number": auto_attempt_number,
        "metadata": (candidate.metadata.model_dump(mode="json") if candidate.metadata is not None else None),
        "pre_recovery_snapshot": {
            "process_running": snapshot.process_running,
            "paused": snapshot.paused,
            "active_runs_by_plane": sorted(plane.value for plane in snapshot.active_runs_by_plane),
            "queue_depth_execution": snapshot.queue_depth_execution,
            "queue_depth_planning": snapshot.queue_depth_planning,
            "queue_depth_learning": snapshot.queue_depth_learning,
        },
    }
    destination.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return destination


def _path_relative_to_root(paths: WorkspacePaths, path: str | Path | None) -> str | None:
    if path is None:
        return None
    resolved = Path(path)
    try:
        return resolved.resolve().relative_to(paths.root).as_posix()
    except (OSError, ValueError):
        return str(path)


__all__ = [
    "BlockedTaskRequeueResult",
    "BlockedWorkItemRetryResult",
    "StrandedBlockedDependency",
    "attempt_stranded_dependency_auto_recovery",
    "retry_blocked_task",
    "retry_blocked_work_item",
]
