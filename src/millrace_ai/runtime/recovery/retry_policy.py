"""Blocked retry policy helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from millrace_ai.workspace.paths import WorkspacePaths

from .environmental import auto_retryable_scope

if TYPE_CHECKING:
    from .blocked_metadata import BlockedItemMetadata

AUTO_REQUEUE_FAILURE_CLASSES = frozenset(
    {
        "network_unavailable",
        "provider_unavailable",
        "provider_rate_limited",
        "runner_timeout",
    }
)


def metadata_allows_auto_requeue(metadata: "BlockedItemMetadata | None") -> bool:
    return (
        metadata is not None
        and metadata.auto_requeue_candidate
        and metadata.failure_class in AUTO_REQUEUE_FAILURE_CLASSES
        and auto_retryable_scope(metadata.failure_scope)
    )


def count_auto_requeues(
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


__all__ = [
    "AUTO_REQUEUE_FAILURE_CLASSES",
    "count_auto_requeues",
    "metadata_allows_auto_requeue",
]
