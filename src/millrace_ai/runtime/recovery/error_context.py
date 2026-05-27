"""Runtime error-context persistence helpers."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from millrace_ai.contracts import ActiveRunState, RuntimeErrorContext, RuntimeSnapshot
from millrace_ai.workspace.paths import WorkspacePaths


def load_runtime_error_context(paths: WorkspacePaths) -> RuntimeErrorContext | None:
    path = paths.runtime_error_context_file
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    return RuntimeErrorContext.model_validate(payload)


def save_runtime_error_context(paths: WorkspacePaths, context: RuntimeErrorContext) -> None:
    _atomic_write_text(paths.runtime_error_context_file, context.model_dump_json(indent=2) + "\n")


def clear_runtime_error_context(paths: WorkspacePaths) -> None:
    if paths.runtime_error_context_file.exists():
        paths.runtime_error_context_file.unlink()


def report_path_for(*, paths: WorkspacePaths, run_id: str) -> Path:
    return paths.runs_dir / run_id / "runtime_error_report.md"


def context_matches_snapshot(context: RuntimeErrorContext, snapshot: RuntimeSnapshot) -> bool:
    return (
        snapshot.current_failure_class == context.error_code.value
        and snapshot.active_plane is context.plane
        and snapshot.active_stage == context.repair_stage
        and snapshot.active_run_id == context.run_id
        and snapshot.active_work_item_family_id == context.work_item_family_id
        and snapshot.active_work_item_id == context.work_item_id
    )


def context_matches_active_run(
    context: RuntimeErrorContext,
    active_run: ActiveRunState,
) -> bool:
    return (
        active_run.plane is context.plane
        and active_run.stage == context.repair_stage
        and active_run.run_id == context.run_id
        and active_run.work_item_family_id == context.work_item_family_id
        and active_run.work_item_id == context.work_item_id
    )


def _atomic_write_text(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.tmp-{uuid4().hex}")
    try:
        with temp_path.open("w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
        temp_path.replace(path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


__all__ = [
    "clear_runtime_error_context",
    "context_matches_active_run",
    "context_matches_snapshot",
    "load_runtime_error_context",
    "report_path_for",
    "save_runtime_error_context",
]
