"""Compatibility facade for blocked recovery helpers."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from millrace_ai.contracts import StageResultEnvelope, WorkItemKind
from millrace_ai.workspace.family_adapters import (
    queue_adapter_for_family_id,
    queue_adapter_for_id,
    resolve_queue_lifecycle_adapter_id,
)
from millrace_ai.workspace.paths import WorkspacePaths

from .recovery import blocked_metadata as _blocked_metadata
from .recovery import queue_mutation as _queue_mutation
from .recovery.blocked_metadata import BlockedItemMetadata, blocked_metadata_path, load_blocked_metadata
from .recovery.queue_mutation import BlockedTaskRequeueResult, BlockedWorkItemRetryResult
from .recovery.retry_policy import AUTO_REQUEUE_FAILURE_CLASSES

if TYPE_CHECKING:
    from millrace_ai.config import RuntimeConfig
    from millrace_ai.router import RouterDecision
    from millrace_ai.runtime.engine import RuntimeEngine


def _sync_family_adapter_resolvers() -> None:
    _blocked_metadata.QUEUE_ADAPTER_FOR_ID = queue_adapter_for_id
    _blocked_metadata.QUEUE_ADAPTER_FOR_FAMILY_ID = queue_adapter_for_family_id
    _blocked_metadata.RESOLVE_QUEUE_LIFECYCLE_ADAPTER_ID = resolve_queue_lifecycle_adapter_id
    _queue_mutation.QUEUE_ADAPTER_FOR_ID = queue_adapter_for_id
    _queue_mutation.QUEUE_ADAPTER_FOR_FAMILY_ID = queue_adapter_for_family_id
    _queue_mutation.RESOLVE_QUEUE_LIFECYCLE_ADAPTER_ID = resolve_queue_lifecycle_adapter_id


def write_blocked_item_metadata(
    paths: WorkspacePaths,
    *,
    stage_result: StageResultEnvelope,
    decision: RouterDecision,
    stage_result_path: Path | None = None,
    now: datetime | None = None,
) -> Path:
    _sync_family_adapter_resolvers()
    return _blocked_metadata.write_blocked_item_metadata(
        paths,
        stage_result=stage_result,
        decision=decision,
        stage_result_path=stage_result_path,
        now=now,
    )


def build_blocked_item_metadata(
    paths: WorkspacePaths,
    *,
    stage_result: StageResultEnvelope,
    decision: RouterDecision,
    stage_result_path: Path | None = None,
    now: datetime | None = None,
) -> BlockedItemMetadata:
    _sync_family_adapter_resolvers()
    return _blocked_metadata.build_blocked_item_metadata(
        paths,
        stage_result=stage_result,
        decision=decision,
        stage_result_path=stage_result_path,
        now=now,
    )


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
    _sync_family_adapter_resolvers()
    return _queue_mutation.retry_blocked_work_item(
        paths,
        work_item_id=work_item_id,
        reason=reason,
        actor=actor,
        auto=auto,
        work_item_family_id=work_item_family_id,
        work_item_kind=work_item_kind,
        force=force,
        root_spec_id=root_spec_id,
        config=config,
        diagnostics_path=diagnostics_path,
    )


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
    _sync_family_adapter_resolvers()
    return _queue_mutation.retry_blocked_task(
        paths,
        task_id=task_id,
        reason=reason,
        actor=actor,
        auto=auto,
        force=force,
        root_spec_id=root_spec_id,
        config=config,
        diagnostics_path=diagnostics_path,
    )


def attempt_stranded_dependency_auto_recovery(
    engine: RuntimeEngine,
) -> BlockedTaskRequeueResult | None:
    _sync_family_adapter_resolvers()
    return _queue_mutation.attempt_stranded_dependency_auto_recovery(engine)


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
