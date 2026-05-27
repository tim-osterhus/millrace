"""Work-item helpers for runtime-effect operation runners."""

from __future__ import annotations

from pathlib import Path

from millrace_ai.contracts import StageResultEnvelope, WorkItemKind
from millrace_ai.contracts.work_documents import TaskDocument
from millrace_ai.errors import QueueStateError
from millrace_ai.workspace.paths import WorkspacePaths
from millrace_ai.workspace.queue_transitions import enqueue_task

from ..models import SourceLifecycleAction, SourceLifecycleIntent


def stage_result_work_item_kind(
    stage_result: StageResultEnvelope,
    *,
    context: str = "runtime effect",
) -> WorkItemKind:
    if stage_result.work_item_kind is None:
        raise QueueStateError(f"{context} requires stage result work_item_kind")
    return stage_result.work_item_kind


def source_lifecycle_intent(
    stage_result: StageResultEnvelope,
    *,
    plan_id: str,
    action: SourceLifecycleAction,
    context: str = "runtime effect",
) -> SourceLifecycleIntent:
    return SourceLifecycleIntent(
        lifecycle_plan_id=plan_id,
        action=action,
        work_item_kind=stage_result_work_item_kind(stage_result, context=context),
        work_item_id=stage_result.work_item_id,
    )


def enqueue_task_document(paths: WorkspacePaths, task: TaskDocument) -> Path:
    return enqueue_task(paths, task)


__all__ = [
    "enqueue_task_document",
    "source_lifecycle_intent",
    "stage_result_work_item_kind",
]
