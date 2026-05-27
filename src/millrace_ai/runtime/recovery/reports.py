"""Runtime error report rendering and request-field projection."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from millrace_ai.contracts import Plane, RuntimeErrorContext
from millrace_ai.workspace.paths import WorkspacePaths

from .error_context import (
    context_matches_active_run,
    context_matches_snapshot,
    load_runtime_error_context,
)

BLOCKED_MARKER = "### BLOCKED"
_ERROR_CATALOG_RELATIVE_PATH = Path("docs/runtime/millrace-runtime-error-codes.md")

if TYPE_CHECKING:
    from millrace_ai.runtime.engine import RuntimeEngine


def build_runtime_error_request_fields(
    engine: RuntimeEngine,
    *,
    plane: Plane | None = None,
) -> dict[str, str | None]:
    fields: dict[str, str | None] = {
        "runtime_error_code": None,
        "runtime_error_report_path": None,
        "runtime_error_catalog_path": None,
    }
    snapshot = engine.snapshot
    if snapshot is None:
        return fields

    context = load_runtime_error_context(engine.paths)
    if context is None:
        return fields

    if plane is not None:
        from millrace_ai.runtime.active_runs import active_run_for_plane

        active_run = active_run_for_plane(snapshot, plane)
        if active_run is None or not context_matches_active_run(context, active_run):
            return fields
    elif not context_matches_snapshot(context, snapshot):
        return fields

    catalog_path = runtime_error_catalog_path(engine.paths)
    fields["runtime_error_code"] = context.error_code.value
    fields["runtime_error_report_path"] = context.report_path
    fields["runtime_error_catalog_path"] = str(catalog_path) if catalog_path is not None else None
    return fields


def runtime_error_catalog_path(paths: WorkspacePaths) -> Path | None:
    catalog_path = paths.root / _ERROR_CATALOG_RELATIVE_PATH
    if not catalog_path.is_file():
        return None
    return catalog_path


def render_runtime_error_report(context: RuntimeErrorContext) -> str:
    lines = [
        "# Runtime Error Report",
        "",
        f"Error-Code: {context.error_code.value}",
        f"Plane: {context.plane.value}",
        f"Failed-Stage: {context.failed_stage.value}",
        f"Repair-Stage: {context.repair_stage.value}",
        f"Run-ID: {context.run_id}",
        f"Work-Item: {context.work_item_family_id} {context.work_item_id}",
        f"Router-Action: {context.router_action or 'none'}",
        f"Terminal-Result: {context.terminal_result.value if context.terminal_result else 'none'}",
        f"Stage-Result-Path: {context.stage_result_path or 'none'}",
        f"Exception-Type: {context.exception_type}",
        f"Exception-Message: {context.exception_message}",
        f"Failure-Origin: {context.failure_origin.value if context.failure_origin else 'none'}",
        f"Captured-At: {context.captured_at.isoformat()}",
        "",
        "Summary:",
        "- The runtime hit an exception after a stage returned a legal terminal result.",
        "- Runtime-owned handling either stopped this work item or rerouted it according to the error code.",
        "- Consult the runtime error catalog when the error code needs interpretation.",
    ]
    return "\n".join(lines) + "\n"


def write_runtime_error_report(paths: WorkspacePaths, context: RuntimeErrorContext) -> None:
    _atomic_write_text(Path(context.report_path), render_runtime_error_report(context))


def path_relative_to_root(paths: WorkspacePaths, path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        return str(path.relative_to(paths.root))
    except ValueError:
        return str(path)


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
    "BLOCKED_MARKER",
    "build_runtime_error_request_fields",
    "path_relative_to_root",
    "render_runtime_error_report",
    "runtime_error_catalog_path",
    "write_runtime_error_report",
]
