"""Read-only queue depth readers."""

from __future__ import annotations

from pathlib import Path

from millrace_ai.paths import workspace_paths

from millrace_web.models import QueueBucket, QueueSummary, WorkspaceRef


def read_queue_summary(workspace: WorkspaceRef) -> QueueSummary:
    paths = workspace_paths(workspace.path)
    return QueueSummary(
        tasks=QueueBucket(
            incoming=_count_files(paths.tasks_queue_dir),
            active=_count_files(paths.tasks_active_dir),
            done=_count_files(paths.tasks_done_dir),
            blocked=_count_files(paths.tasks_blocked_dir),
        ),
        specs=QueueBucket(
            incoming=_count_files(paths.specs_queue_dir),
            active=_count_files(paths.specs_active_dir),
            done=_count_files(paths.specs_done_dir),
            blocked=_count_files(paths.specs_blocked_dir),
        ),
        incidents=QueueBucket(
            incoming=_count_files(paths.incidents_incoming_dir),
            active=_count_files(paths.incidents_active_dir),
            done=_count_files(paths.incidents_resolved_dir),
            blocked=_count_files(paths.incidents_blocked_dir),
        ),
        learning=QueueBucket(
            incoming=_count_files(paths.learning_requests_queue_dir),
            active=_count_files(paths.learning_requests_active_dir),
            done=_count_files(paths.learning_requests_done_dir),
            blocked=_count_files(paths.learning_requests_blocked_dir),
        ),
    )


def _count_files(path: Path) -> int:
    if not path.is_dir():
        return 0
    return sum(1 for item in path.iterdir() if item.is_file() and not item.name.startswith("."))

