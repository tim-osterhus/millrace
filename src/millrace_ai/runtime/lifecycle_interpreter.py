"""Runtime-facing source lifecycle interpreter helpers."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from millrace_ai.architecture import CompiledRunPlan
from millrace_ai.workspace.paths import WorkspacePaths
from millrace_ai.workspace.queue_lifecycle import QueueLifecycleInterpreter

if TYPE_CHECKING:
    from millrace_ai.runtime.effects import SourceLifecycleIntent


def apply_source_lifecycle_intent(
    paths: WorkspacePaths,
    intent: "SourceLifecycleIntent",
    *,
    compiled_plan: CompiledRunPlan | None = None,
) -> Path:
    work_item_families = (
        tuple(compiled_plan.work_item_families_by_id.values())
        if compiled_plan is not None and compiled_plan.work_item_families_by_id
        else None
    )
    return QueueLifecycleInterpreter(paths, work_item_families=work_item_families).apply(intent)


__all__ = ["apply_source_lifecycle_intent"]
