"""Blocked work-item recovery metadata and retry helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, TypeAlias, cast

from pydantic import JsonValue, ValidationError, model_validator

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
    StageResultEnvelope,
    TaskDocument,
    WorkItemKind,
)
from millrace_ai.contracts.base import ContractModel
from millrace_ai.contracts.work_refs import coerce_family_and_kind, family_id_for_work_item_kind, normalize_work_item_family_id
from millrace_ai.errors import QueueStateError
from millrace_ai.events import write_runtime_event
from millrace_ai.queue_store import QueueStore
from millrace_ai.state_store import load_snapshot, save_snapshot
from millrace_ai.workspace.family_adapters import (
    queue_adapter_for_family_id,
    queue_adapter_for_id,
    resolve_queue_lifecycle_adapter_id,
)
from millrace_ai.workspace.lineage_integrity import effective_root_spec_id
from millrace_ai.workspace.paths import WorkspacePaths
from millrace_ai.workspace.work_documents import parse_work_document_as
from millrace_ai.workspace.work_inventory import queue_depths_by_plane

if TYPE_CHECKING:
    from millrace_ai.router import RouterDecision
    from millrace_ai.runtime.engine import RuntimeEngine
    from millrace_ai.workspace.family_adapters import WorkFamilyQueueAdapter

BlockedOrigin = Literal[
    "stage_terminal",
    "runner_failure",
    "runtime_exception",
    "operator",
    "unknown",
]
FailureScope = Literal[
    "environment",
    "provider",
    "local_configuration",
    "contract",
    "semantic",
    "unknown",
]

QueueDocumentModel: TypeAlias = (
    type[TaskDocument]
    | type[SpecDocument]
    | type[ProbeDocument]
    | type[IncidentDocument]
    | type[LearningRequestDocument]
    | type[BlueprintDraftDocument]
)

AUTO_REQUEUE_FAILURE_CLASSES = frozenset(
    {
        "network_unavailable",
        "provider_unavailable",
        "provider_rate_limited",
        "runner_timeout",
    }
)


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


class BlockedTaskRequeueResult(ContractModel):
    task_id: str
    source_path: str
    destination_path: str
    source_state: Literal["blocked"] = "blocked"
    destination_state: Literal["queue"] = "queue"
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
    source_state: Literal["blocked"] = "blocked"
    destination_state: Literal["queue"] = "queue"
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
    decision: RouterDecision,
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
    decision: RouterDecision,
    stage_result_path: Path | None = None,
    now: datetime | None = None,
) -> BlockedItemMetadata:
    root_idea_id, root_spec_id = _blocked_work_item_lineage(paths, stage_result)
    failure_class = _metadata_string(stage_result.metadata.get("failure_class"))
    if failure_class is None:
        failure_class = decision.failure_class or "stage_declared_blocked"
    blocked_origin = _blocked_origin_for_stage_result(stage_result)
    failure_scope = _failure_scope_for_stage_result(stage_result, blocked_origin=blocked_origin)
    auto_requeue_candidate = (
        bool(stage_result.metadata.get("auto_requeue_candidate")) and failure_class in AUTO_REQUEUE_FAILURE_CLASSES
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

    metadata = load_blocked_metadata(paths, family_id=family.family_id, kind=family.work_item_kind, work_item_id=work_item_id)
    retryable = _metadata_allows_auto_requeue(metadata)
    if not force and not retryable:
        label = "blocked task" if family.family_id == WorkItemKind.TASK.value else "blocked work item"
        raise QueueStateError(f"{label} is not retryable; rerun with --force to override")

    auto_attempts = _count_auto_requeues(paths, queue_dir=family.queue_dir, work_item_id=work_item_id)
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
    _emit_blocked_retry_events(paths, result)
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
    if not _metadata_allows_auto_requeue(metadata):
        _emit_auto_recovery_skipped(
            engine,
            candidate,
            reason="blocked_dependency_not_retryable",
        )
        return None
    assert metadata is not None

    auto_attempts = _count_auto_requeues(engine.paths, task_id=candidate.blocked_task_id)
    if auto_attempts >= policy.max_auto_requeues_per_work_item:
        _emit_auto_recovery_skipped(engine, candidate, reason="retry_budget_exhausted")
        return None
    now = engine._now()
    cooldown = policy.cooldown_seconds[min(auto_attempts, len(policy.cooldown_seconds) - 1)]
    elapsed = (now - metadata.blocked_at).total_seconds()
    if elapsed < cooldown:
        _emit_auto_recovery_skipped(engine, candidate, reason="cooldown_active")
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
        missing_dependencies = tuple(dependency for dependency in queued.depends_on if dependency not in completed)
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
    if family.done_dir is not None and (
        family.done_dir / f"{work_item_id}{family.file_extension}"
    ).exists():
        raise QueueStateError(f"{family.family_id} {work_item_id} is already done")


def _blocked_retry_active_path(
    paths: WorkspacePaths,
    *,
    family: _BlockedRetryFamily,
    work_item_id: str,
) -> Path | None:
    if family.queue_adapter is not None:
        return family.queue_adapter.active_path(
            paths,
            work_item_id=work_item_id,
        )
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
    if model is ProbeDocument:
        return parse_work_document_as(raw, model=ProbeDocument, path=path)
    if model is IncidentDocument:
        return parse_work_document_as(raw, model=IncidentDocument, path=path)
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


def _emit_blocked_retry_events(paths: WorkspacePaths, result: BlockedWorkItemRetryResult) -> None:
    payload: dict[str, JsonValue] = {
        "work_item_family_id": result.work_item_family_id,
        "work_item_kind": result.work_item_kind.value if result.work_item_kind is not None else None,
        "work_item_id": result.work_item_id,
        "actor": result.actor,
        "auto": result.auto,
        "reason": result.reason,
        "failure_class": result.failure_class,
        "attempt_number": result.attempt_number,
        "source_state": result.source_state,
        "destination_state": result.destination_state,
    }
    write_runtime_event(paths, event_type="blocked_work_item_requeued", data=payload)
    if result.work_item_family_id == WorkItemKind.TASK.value:
        write_runtime_event(
            paths,
            event_type="blocked_task_requeued",
            data={
                "task_id": result.work_item_id,
                "actor": result.actor,
                "auto": result.auto,
                "reason": result.reason,
                "failure_class": result.failure_class,
                "attempt_number": result.attempt_number,
                "source_state": result.source_state,
                "destination_state": result.destination_state,
            },
        )


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
    adapter_id = resolve_queue_lifecycle_adapter_id(family)
    if adapter_id is not None:
        adapter = queue_adapter_for_id(adapter_id)
        if adapter is None:
            raise QueueStateError(
                f"work item family {family.family_id} references unknown adapter {adapter_id}"
            )
        return adapter
    return queue_adapter_for_family_id(family.family_id)


def _metadata_allows_auto_requeue(metadata: BlockedItemMetadata | None) -> bool:
    return (
        metadata is not None
        and metadata.auto_requeue_candidate
        and metadata.failure_class in AUTO_REQUEUE_FAILURE_CLASSES
        and metadata.failure_scope not in {"semantic", "contract"}
    )


def _count_auto_requeues(
    paths: WorkspacePaths,
    *,
    task_id: str | None = None,
    queue_dir: Path | None = None,
    work_item_id: str | None = None,
) -> int:
    if task_id is not None:
        queue_dir = paths.tasks_queue_dir
        work_item_id = task_id
    if queue_dir is None or work_item_id is None:
        raise ValueError("queue_dir and work_item_id are required")
    log_path = queue_dir / f"{work_item_id}.requeue.jsonl"
    if not log_path.is_file():
        return 0
    count = 0
    for line in log_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("auto") is True:
            count += 1
    return count


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


def _emit_auto_recovery_skipped(
    engine: RuntimeEngine,
    candidate: StrandedBlockedDependency,
    *,
    reason: str,
) -> None:
    write_runtime_event(
        engine.paths,
        event_type="blocked_dependency_auto_requeue_skipped",
        data={
            "task_id": candidate.blocked_task_id,
            "queued_dependents": list(candidate.queued_dependent_ids),
            "reason": reason,
            "failure_class": (candidate.metadata.failure_class if candidate.metadata is not None else None),
        },
    )
    engine._emit_monitor_event(
        "blocked_lineage_requires_operator_review",
        task_id=candidate.blocked_task_id,
        queued_dependents=list(candidate.queued_dependent_ids),
        reason=reason,
    )


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
    except (OSError, ValidationError, ValueError):
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
        paths.runtime_root
        / family.queue_dirs.blocked
        / f"{work_item_id}{family.file_extension}",
        _lineage_active_path(paths, family=family, work_item_id=work_item_id),
        paths.runtime_root
        / family.queue_dirs.queue
        / f"{work_item_id}{family.file_extension}",
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
        return _metadata_string(payload.get("root_idea_id")), _metadata_string(
            payload.get("root_spec_id")
        )
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


def _blocked_origin_for_stage_result(stage_result: StageResultEnvelope) -> BlockedOrigin:
    raw_origin = stage_result.metadata.get("blocked_origin")
    if isinstance(raw_origin, str) and raw_origin in {
        "stage_terminal",
        "runner_failure",
        "runtime_exception",
        "operator",
        "unknown",
    }:
        return cast(BlockedOrigin, raw_origin)
    if stage_result.metadata.get("normalization_source") == "failure":
        return "runner_failure"
    return "stage_terminal"


def _failure_scope_for_stage_result(
    stage_result: StageResultEnvelope,
    *,
    blocked_origin: BlockedOrigin,
) -> FailureScope:
    raw_scope = stage_result.metadata.get("failure_scope")
    if isinstance(raw_scope, str) and raw_scope in {
        "environment",
        "provider",
        "local_configuration",
        "contract",
        "semantic",
        "unknown",
    }:
        return cast(FailureScope, raw_scope)
    if blocked_origin == "stage_terminal":
        return "semantic"
    return "unknown"


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
    "AUTO_REQUEUE_FAILURE_CLASSES",
    "BlockedItemMetadata",
    "BlockedTaskRequeueResult",
    "BlockedWorkItemRetryResult",
    "attempt_stranded_dependency_auto_recovery",
    "blocked_metadata_path",
    "build_blocked_item_metadata",
    "load_blocked_metadata",
    "retry_blocked_task",
    "retry_blocked_work_item",
    "write_blocked_item_metadata",
]
