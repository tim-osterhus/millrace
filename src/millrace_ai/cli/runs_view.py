"""Run-list view loading and line rendering."""

from __future__ import annotations

from millrace_ai.paths import WorkspacePaths

from .formatting import _value


def _render_runs_ls_lines(paths: WorkspacePaths) -> tuple[str, ...]:
    from millrace_ai.cli.shared import _cli_api

    lines: list[str] = []
    for index, summary in enumerate(_cli_api().list_runs(paths)):
        if index > 0:
            lines.append("")
        lines.extend(
            (
                f"run_id: {summary.run_id}",
                f"status: {summary.status}",
                f"work_item_kind: {_value(summary.work_item_kind)}",
                f"work_item_id: {_value(summary.work_item_id)}",
                f"failure_class: {_value(summary.failure_class)}",
            )
        )
    return tuple(lines)


__all__ = ["_render_runs_ls_lines"]
