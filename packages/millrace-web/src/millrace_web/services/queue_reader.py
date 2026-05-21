"""Read-only queue depth readers."""

from __future__ import annotations

from millrace_ai.compilation.persistence import load_existing_plan
from millrace_ai.paths import workspace_paths
from millrace_ai.workspace.work_inventory import family_counts

from millrace_web.models import QueueBucket, QueueSummary, WorkspaceRef


def read_queue_summary(workspace: WorkspaceRef) -> QueueSummary:
    paths = workspace_paths(workspace.path)
    compiled_plan = load_existing_plan(paths.state_dir / "compiled_plan.json")
    counts_by_family = family_counts(paths, compiled_plan=compiled_plan)
    graph_owned_families = {
        family_id: _bucket_from_counts(counts)
        for family_id, counts in sorted(counts_by_family.items())
    }
    return QueueSummary(
        tasks=graph_owned_families.get("task", QueueBucket()),
        specs=graph_owned_families.get("spec", QueueBucket()),
        incidents=graph_owned_families.get("incident", QueueBucket()),
        learning=graph_owned_families.get("learning_request", QueueBucket()),
        blueprint_drafts=graph_owned_families.get("blueprint_draft", QueueBucket()),
        graph_owned_families=graph_owned_families,
    )


def _bucket_from_counts(counts: dict[str, int]) -> QueueBucket:
    return QueueBucket(
        incoming=counts.get("queue", 0),
        active=counts.get("active", 0),
        done=counts.get("done", 0),
        blocked=counts.get("blocked", 0),
    )
