"""Compiled-plan archive helpers for active-run launch authority."""

from __future__ import annotations

from pathlib import Path

from millrace_ai.architecture import CompiledRunPlan
from millrace_ai.compilation.persistence import atomic_write_json, load_existing_plan
from millrace_ai.errors import WorkspaceStateError
from millrace_ai.paths import WorkspacePaths


class CompiledPlanAuthorityError(WorkspaceStateError):
    """Raised when an active run's launch compiled plan cannot be trusted."""

    def __init__(self, message: str, *, stale: bool) -> None:
        self.stale = stale
        super().__init__(message)


def archive_compiled_plan(paths: WorkspacePaths, compiled_plan: CompiledRunPlan) -> Path:
    """Persist an immutable copy of a compiled plan by id for active-run result application."""

    destination = archived_compiled_plan_path(paths, compiled_plan.compiled_plan_id)
    atomic_write_json(destination, compiled_plan.model_dump(mode="json"))
    return destination


def load_compiled_plan_by_id(paths: WorkspacePaths, compiled_plan_id: str) -> CompiledRunPlan | None:
    """Load a compiled plan by id from current state or the immutable archive."""

    current = load_existing_plan(paths.state_dir / "compiled_plan.json")
    if current is not None and current.compiled_plan_id == compiled_plan_id:
        return current
    return load_existing_plan(archived_compiled_plan_path(paths, compiled_plan_id))


def archived_compiled_plan_path(paths: WorkspacePaths, compiled_plan_id: str) -> Path:
    safe_id = "".join(char if char.isalnum() or char in {"-", "_", "."} else "-" for char in compiled_plan_id)
    return paths.state_dir / "compiled_plans" / f"{safe_id}.json"


def relative_plan_path(paths: WorkspacePaths, path: Path) -> str:
    try:
        return str(path.relative_to(paths.root))
    except ValueError:
        return str(path)


__all__ = [
    "CompiledPlanAuthorityError",
    "archive_compiled_plan",
    "archived_compiled_plan_path",
    "load_compiled_plan_by_id",
    "relative_plan_path",
]
