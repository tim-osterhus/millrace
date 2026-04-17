"""Pure CLI rendering and output helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

import typer

from millrace_ai.compiler import CompileOutcome
from millrace_ai.config import RuntimeConfig
from millrace_ai.contracts import CompileDiagnostics, ResultClass, TokenUsage
from millrace_ai.control import ControlActionResult
from millrace_ai.paths import WorkspacePaths
from millrace_ai.run_inspection import InspectedRunSummary
from millrace_ai.runtime import RuntimeTickOutcome
from millrace_ai.state_store import load_snapshot


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


def _render_status_lines(paths: WorkspacePaths) -> tuple[str, ...]:
    snapshot = load_snapshot(paths)

    execution_queue_depth = len(tuple(paths.tasks_queue_dir.glob("*.md")))
    planning_queue_depth = len(tuple(paths.specs_queue_dir.glob("*.md"))) + len(
        tuple(paths.incidents_incoming_dir.glob("*.md"))
    )

    lines = [
        f"workspace: {paths.root}",
        f"runtime_mode: {snapshot.runtime_mode.value}",
        f"process_running: {'true' if snapshot.process_running else 'false'}",
        f"paused: {'true' if snapshot.paused else 'false'}",
        f"stop_requested: {'true' if snapshot.stop_requested else 'false'}",
        f"active_mode_id: {snapshot.active_mode_id}",
        f"compiled_plan_id: {snapshot.compiled_plan_id}",
        f"active_plane: {_value(snapshot.active_plane)}",
        f"active_stage: {_value(snapshot.active_stage)}",
        f"active_work_item_kind: {_value(snapshot.active_work_item_kind)}",
        f"active_work_item_id: {_value(snapshot.active_work_item_id)}",
        f"execution_queue_depth: {execution_queue_depth}",
        f"planning_queue_depth: {planning_queue_depth}",
        f"execution_status_marker: {snapshot.execution_status_marker}",
        f"planning_status_marker: {snapshot.planning_status_marker}",
    ]
    if snapshot.current_failure_class:
        lines.append(f"current_failure_class: {snapshot.current_failure_class}")
        for label, count in (
            ("troubleshoot_attempt_count", snapshot.troubleshoot_attempt_count),
            ("mechanic_attempt_count", snapshot.mechanic_attempt_count),
            ("fix_cycle_count", snapshot.fix_cycle_count),
            ("consultant_invocations", snapshot.consultant_invocations),
        ):
            if count > 0:
                lines.append(f"{label}: {count}")
    return tuple(lines)


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


def _render_run_show_lines(summary: InspectedRunSummary) -> tuple[str, ...]:
    lines = [
        f"run_id: {summary.run_id}",
        f"status: {summary.status}",
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
                f"stage: {stage_result.stage}",
                f"terminal_result: {stage_result.terminal_result}",
                f"result_class: {stage_result.result_class}",
                f"runner_name: {_value(stage_result.runner_name)}",
                f"model_name: {_value(stage_result.model_name)}",
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


def _render_config_show_lines(paths: WorkspacePaths, config: RuntimeConfig) -> tuple[str, ...]:
    snapshot = load_snapshot(paths)
    return (
        f"default_mode: {config.runtime.default_mode}",
        f"run_style: {config.runtime.run_style.value}",
        f"idle_sleep_seconds: {config.runtime.idle_sleep_seconds}",
        f"watchers.enabled: {'true' if config.watchers.enabled else 'false'}",
        f"config_version: {snapshot.config_version}",
        f"last_reload_outcome: {_value(snapshot.last_reload_outcome)}",
        f"last_reload_error: {_value(snapshot.last_reload_error)}",
    )


def _print_status(paths: WorkspacePaths) -> None:
    for line in _render_status_lines(paths):
        typer.echo(line)


def _print_statuses(paths_list: Sequence[WorkspacePaths]) -> None:
    for index, paths in enumerate(paths_list):
        if index > 0:
            typer.echo("")
        _print_status(paths)


def _render_compile_diagnostics(outcome: CompileOutcome) -> int:
    diagnostics: CompileDiagnostics = outcome.diagnostics
    typer.echo(f"ok: {'true' if diagnostics.ok else 'false'}")
    typer.echo(f"mode_id: {diagnostics.mode_id}")
    typer.echo(f"used_last_known_good: {'true' if outcome.used_last_known_good else 'false'}")
    for warning in diagnostics.warnings:
        typer.echo(f"warning: {warning}")
    for error in diagnostics.errors:
        typer.echo(f"error: {error}")
    return 0 if diagnostics.ok else 1


def _print_error(message: str) -> int:
    typer.echo(f"error: {message}")
    return 1


__all__ = [
    "_print_control_result",
    "_print_error",
    "_print_status",
    "_print_statuses",
    "_render_compile_diagnostics",
    "_render_config_show_lines",
    "_render_run_show_lines",
    "_render_runs_ls_lines",
    "_render_status_lines",
    "_resolve_run_artifact_path",
    "_run_once_exit_code",
    "_value",
]
