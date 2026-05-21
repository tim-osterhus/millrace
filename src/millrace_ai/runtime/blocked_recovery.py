"""Blocked work-item recovery metadata and retry helpers."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import ValidationError, model_validator

from millrace_ai.compilation.persistence import load_existing_plan
from millrace_ai.config import RuntimeConfig
from millrace_ai.contracts import Plane, RuntimeSnapshot, StageResultEnvelope, TaskDocument, WorkItemKind
from millrace_ai.contracts.base import ContractModel
from millrace_ai.contracts.work_refs import family_id_for_work_item_kind, normalize_work_item_family_id
from millrace_ai.errors import QueueStateError
from millrace_ai.events import write_runtime_event
from millrace_ai.queue_store import QueueStore
from millrace_ai.state_store import load_snapshot, save_snapshot
from millrace_ai.workspace.lineage_integrity import effective_root_spec_id
from millrace_ai.workspace.paths import WorkspacePaths
from millrace_ai.workspace.work_documents import parse_work_document_as
from millrace_ai.workspace.work_inventory import queue_depths_by_plane

if TYPE_CHECKING:
    from millrace_ai.router import RouterDecision
    from millrace_ai.runtime.engine import RuntimeEngine

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


class StrandedBlockedDependency(ContractModel):
    blocked_task_id: str
    queued_dependent_ids: tuple[str, ...]
    root_spec_id: str | None = None
    metadata: BlockedItemMetadata | None = None


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
    task_id: str,
) -> BlockedItemMetadata | None:
    path = blocked_metadata_path(paths, kind=WorkItemKind.TASK, work_item_id=task_id)
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
    cleaned_reason = reason.strip()
    if not cleaned_reason:
        raise QueueStateError("requeue reason is required")
    source_path = paths.tasks_blocked_dir / f"{task_id}.md"
    destination_path = paths.tasks_queue_dir / f"{task_id}.md"
    _validate_blocked_task_locations(paths, task_id)
    task = parse_work_document_as(source_path.read_text(encoding="utf-8"), model=TaskDocument, path=source_path)
    task_root_spec_id = effective_root_spec_id(task)
    if root_spec_id is not None and task_root_spec_id != root_spec_id:
        raise QueueStateError(f"blocked task {task_id} does not belong to root spec {root_spec_id}")

    metadata = load_blocked_metadata(paths, task_id=task_id)
    retryable = _metadata_allows_auto_requeue(metadata)
    if not force and not retryable:
        raise QueueStateError("blocked task is not retryable; rerun with --force to override")

    auto_attempts = _count_auto_requeues(paths, task_id=task_id)
    max_attempts = config.auto_recovery.max_auto_requeues_per_work_item if config is not None else 3
    if not force and auto_attempts >= max_attempts:
        raise QueueStateError("blocked task retry budget is exhausted")

    attempt_number = auto_attempts + 1
    failure_class = metadata.failure_class if metadata is not None else None
    QueueStore(paths).requeue_blocked_task(
        task_id,
        reason=cleaned_reason,
        actor=actor,
        auto=auto,
        failure_class=failure_class,
        attempt_number=attempt_number,
    )
    _refresh_snapshot_queue_depths(paths)
    result = BlockedTaskRequeueResult(
        task_id=task_id,
        source_path=str(source_path),
        destination_path=str(destination_path),
        actor=actor,
        auto=auto,
        reason=cleaned_reason,
        failure_class=failure_class,
        attempt_number=attempt_number,
        diagnostics_path=str(diagnostics_path) if diagnostics_path is not None else None,
    )
    write_runtime_event(
        paths,
        event_type="blocked_task_requeued",
        data={
            "task_id": task_id,
            "actor": actor,
            "auto": auto,
            "reason": reason,
            "failure_class": failure_class,
            "attempt_number": attempt_number,
            "source_state": "blocked",
            "destination_state": "queue",
        },
    )
    return result


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


def _validate_blocked_task_locations(paths: WorkspacePaths, task_id: str) -> None:
    blocked_path = paths.tasks_blocked_dir / f"{task_id}.md"
    if not blocked_path.is_file():
        raise QueueStateError(f"task {task_id} is not blocked")
    for state, directory in (
        ("queue", paths.tasks_queue_dir),
        ("active", paths.tasks_active_dir),
        ("done", paths.tasks_done_dir),
    ):
        if (directory / f"{task_id}.md").exists():
            raise QueueStateError(f"task {task_id} is already {state}")


def _metadata_allows_auto_requeue(metadata: BlockedItemMetadata | None) -> bool:
    return metadata is not None and metadata.auto_requeue_candidate and metadata.failure_class in AUTO_REQUEUE_FAILURE_CLASSES


def _count_auto_requeues(paths: WorkspacePaths, *, task_id: str) -> int:
    log_path = paths.tasks_queue_dir / f"{task_id}.requeue.jsonl"
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
        if family_id == WorkItemKind.BLUEPRINT_DRAFT.value:
            return tuple(
                paths.runtime_root / "blueprints" / "drafts" / state / f"{work_item_id}.json"
                for state in ("blocked", "active", "queue")
            )
        return ()
    candidates: list[Path] = []
    for dir_key in ("blocked", "active", "queue"):
        relative = getattr(family.queue_dirs, dir_key)
        if relative is None:
            continue
        candidates.append(paths.runtime_root / relative / f"{work_item_id}{family.file_extension}")
    return tuple(candidates)


def _work_item_family_for_lineage(paths: WorkspacePaths, family_id: str):
    compiled_plan = load_existing_plan(paths.state_dir / "compiled_plan.json")
    if compiled_plan is not None:
        family = compiled_plan.work_item_families_by_id.get(family_id)
        if family is not None:
            return family
    try:
        from millrace_ai.assets import load_builtin_workflow_primitives
    except Exception:
        return None
    return next(
        (
            family
            for family in load_builtin_workflow_primitives().work_item_families
            if family.family_id == family_id
        ),
        None,
    )


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
    if raw_origin in {"stage_terminal", "runner_failure", "runtime_exception", "operator", "unknown"}:
        return raw_origin
    if stage_result.metadata.get("normalization_source") == "failure":
        return "runner_failure"
    return "stage_terminal"


def _failure_scope_for_stage_result(
    stage_result: StageResultEnvelope,
    *,
    blocked_origin: BlockedOrigin,
) -> FailureScope:
    raw_scope = stage_result.metadata.get("failure_scope")
    if raw_scope in {
        "environment",
        "provider",
        "local_configuration",
        "contract",
        "semantic",
        "unknown",
    }:
        return raw_scope
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
    "attempt_stranded_dependency_auto_recovery",
    "blocked_metadata_path",
    "build_blocked_item_metadata",
    "load_blocked_metadata",
    "retry_blocked_task",
    "write_blocked_item_metadata",
]
