"""Pure CLI rendering and output helpers."""

from __future__ import annotations

from pathlib import Path

import typer

from millrace_ai.contracts import ResultClass, TokenUsage
from millrace_ai.control import ControlActionResult
from millrace_ai.run_inspection import InspectedRunSummary
from millrace_ai.runtime import RuntimeTickOutcome


def _run_once_exit_code(outcome: RuntimeTickOutcome) -> int:
    failure_class = outcome.stage_result.metadata.get("failure_class")
    if isinstance(failure_class, str) and failure_class in {
        "runner_transport_failure",
        "provider_failure",
    }:
        return 1

    if outcome.stage_result.result_class is ResultClass.RECOVERABLE_FAILURE:
        return 1

    return 0


def _value(value: object) -> str:
    if value is None:
        return "none"
    enum_value = getattr(value, "value", None)
    if isinstance(enum_value, str):
        return enum_value
    return str(value)


def _print_control_result(result: ControlActionResult) -> None:
    typer.echo(f"action: {result.action.value}")
    typer.echo(f"mode: {result.mode}")
    typer.echo(f"applied: {'true' if result.applied else 'false'}")
    typer.echo(f"detail: {result.detail}")
    if result.command_id is not None:
        typer.echo(f"command_id: {result.command_id}")
    if result.mailbox_path is not None:
        typer.echo(f"mailbox_path: {result.mailbox_path}")
    if result.artifact_path is not None:
        typer.echo(f"artifact_path: {result.artifact_path}")


def _render_run_show_lines(summary: InspectedRunSummary) -> tuple[str, ...]:
    lines = [
        f"run_id: {summary.run_id}",
        f"status: {summary.status}",
        f"compiled_plan_id: {_value(summary.compiled_plan_id)}",
        f"mode_id: {_value(summary.mode_id)}",
        f"request_kind: {_value(summary.request_kind)}",
        f"closure_target_root_spec_id: {_value(summary.closure_target_root_spec_id)}",
        f"work_item_kind: {_value(summary.work_item_kind)}",
        f"work_item_id: {_value(summary.work_item_id)}",
        f"failure_class: {_value(summary.failure_class)}",
        f"started_at: {_value(summary.started_at)}",
        f"completed_at: {_value(summary.completed_at)}",
        f"duration_seconds: {_value(summary.duration_seconds)}",
        f"troubleshoot_report_path: {_value(summary.troubleshoot_report_path)}",
        f"primary_stdout_path: {_value(summary.primary_stdout_path)}",
        f"primary_stderr_path: {_value(summary.primary_stderr_path)}",
        f"stage_result_count: {len(summary.stage_results)}",
    ]
    lines.extend(_render_token_usage_lines(summary.token_usage))
    for note in summary.notes:
        lines.append(f"note: {note}")
    for stage_result in summary.stage_results:
        lines.extend(
            (
                f"stage_result_path: {stage_result.stage_result_path}",
                f"request_id: {_value(stage_result.request_id)}",
                f"compiled_plan_id: {_value(stage_result.compiled_plan_id)}",
                f"mode_id: {_value(stage_result.mode_id)}",
                f"stage: {stage_result.stage}",
                f"node_id: {stage_result.node_id}",
                f"stage_kind_id: {stage_result.stage_kind_id}",
                f"request_kind: {_value(stage_result.request_kind)}",
                f"closure_target_root_spec_id: {_value(stage_result.closure_target_root_spec_id)}",
                f"terminal_result: {stage_result.terminal_result}",
                f"result_class: {stage_result.result_class}",
                f"runner_name: {_value(stage_result.runner_name)}",
                f"model_name: {_value(stage_result.model_name)}",
                f"model_reasoning_effort: {_value(stage_result.model_reasoning_effort)}",
                f"started_at: {stage_result.started_at}",
                f"completed_at: {stage_result.completed_at}",
                f"duration_seconds: {stage_result.duration_seconds}",
                f"stdout_path: {_value(stage_result.stdout_path)}",
                f"stderr_path: {_value(stage_result.stderr_path)}",
                f"report_artifact: {_value(stage_result.report_artifact)}",
            )
        )
        lines.extend(_render_token_usage_lines(stage_result.token_usage))
        for artifact_path in stage_result.artifact_paths:
            lines.append(f"artifact_path: {artifact_path}")
    return tuple(lines)


def _render_token_usage_lines(token_usage: TokenUsage | None) -> tuple[str, ...]:
    if token_usage is None:
        return ()
    return (
        f"input_tokens: {token_usage.input_tokens}",
        f"cached_input_tokens: {token_usage.cached_input_tokens}",
        f"output_tokens: {token_usage.output_tokens}",
        f"thinking_tokens: {token_usage.thinking_tokens}",
        f"total_tokens: {token_usage.total_tokens}",
    )


def _resolve_run_artifact_path(run_dir: str, candidate: str) -> Path:
    path = Path(candidate)
    if path.is_absolute():
        return path
    return Path(run_dir) / path


__all__ = [
    "_print_control_result",
    "_render_run_show_lines",
    "_resolve_run_artifact_path",
    "_run_once_exit_code",
    "_value",
]
