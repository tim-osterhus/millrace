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
                f"artifact_status: {summary.artifact_status}",
                f"runtime_outcome: {summary.runtime_outcome}",
                f"compiled_plan_id: {_value(summary.compiled_plan_id)}",
                f"work_item_kind: {_value(summary.work_item_kind)}",
                f"work_item_id: {_value(summary.work_item_id)}",
                f"failure_class: {_value(summary.failure_class)}",
                f"failure_origin: {_value(summary.failure_origin)}",
                f"runtime_effect_handler_id: {_value(summary.runtime_effect_handler_id)}",
                f"runtime_effect_decision: {_value(summary.runtime_effect_decision)}",
                f"runtime_effect_failure_class: {_value(summary.runtime_effect_failure_class)}",
                f"runtime_effect_failure_message: {_value(summary.runtime_effect_failure_message)}",
                f"runtime_effect_mutation_phase: {_value(summary.runtime_effect_mutation_phase)}",
                (
                    "runtime_effect_failure_policy_id: "
                    f"{_value(summary.runtime_effect_failure_policy_id)}"
                ),
                f"runtime_effect_recovery_action: {_value(summary.runtime_effect_recovery_action)}",
            )
        )
    return tuple(lines)


__all__ = ["_render_runs_ls_lines"]
