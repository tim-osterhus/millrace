"""Pure CLI rendering and output helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

import typer

from millrace_ai.compiler import CompiledPlanCurrentness, CompileOutcome, inspect_workspace_plan_currentness
from millrace_ai.config import RuntimeConfig, load_runtime_config
from millrace_ai.contracts import CompileDiagnostics, ResultClass, TokenUsage
from millrace_ai.control import ControlActionResult
from millrace_ai.paths import WorkspacePaths
from millrace_ai.run_inspection import InspectedRunSummary
from millrace_ai.runtime import RuntimeTickOutcome
from millrace_ai.state_store import load_snapshot
from millrace_ai.workspace.arbiter_state import list_open_closure_target_states
from millrace_ai.workspace.baseline import BaselineManifest, load_baseline_manifest


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
    baseline_manifest = _load_baseline_manifest_safe(paths)
    currentness, currentness_error = _load_compile_currentness(paths)

    execution_queue_depth = len(tuple(paths.tasks_queue_dir.glob("*.md")))
    planning_queue_depth = len(tuple(paths.specs_queue_dir.glob("*.md"))) + len(
        tuple(paths.incidents_incoming_dir.glob("*.md"))
    )
    learning_queue_depth = len(tuple(paths.learning_requests_queue_dir.glob("*.md")))

    lines = [
        f"workspace: {paths.root}",
        f"runtime_mode: {snapshot.runtime_mode.value}",
        f"process_running: {'true' if snapshot.process_running else 'false'}",
        f"paused: {'true' if snapshot.paused else 'false'}",
        f"stop_requested: {'true' if snapshot.stop_requested else 'false'}",
        f"active_mode_id: {snapshot.active_mode_id}",
        f"compiled_plan_id: {snapshot.compiled_plan_id}",
        f"compiled_plan_currentness: {_compiled_plan_currentness_value(currentness, currentness_error)}",
        f"active_plane: {_value(snapshot.active_plane)}",
        f"active_stage: {_value(snapshot.active_stage)}",
        f"active_node_id: {_value(snapshot.active_node_id)}",
        f"active_stage_kind_id: {_value(snapshot.active_stage_kind_id)}",
        f"active_work_item_kind: {_value(snapshot.active_work_item_kind)}",
        f"active_work_item_id: {_value(snapshot.active_work_item_id)}",
        f"execution_queue_depth: {execution_queue_depth}",
        f"planning_queue_depth: {planning_queue_depth}",
        f"learning_queue_depth: {learning_queue_depth}",
        f"execution_status_marker: {snapshot.execution_status_marker}",
        f"planning_status_marker: {snapshot.planning_status_marker}",
        f"learning_status_marker: {snapshot.learning_status_marker}",
    ]
    lines.extend(_render_baseline_manifest_lines(baseline_manifest))
    lines.extend(_render_compile_currentness_lines(currentness, currentness_error))
    lines.extend(_render_closure_target_status_lines(paths))
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


def _render_closure_target_status_lines(paths: WorkspacePaths) -> tuple[str, ...]:
    open_targets = list_open_closure_target_states(paths)
    if len(open_targets) > 1:
        return (
            "closure_target_root_spec_id: invalid_multiple_open_targets",
            "closure_target_open: invalid",
            "closure_target_blocked_by_lineage_work: invalid",
            "closure_target_latest_verdict_path: none",
            "closure_target_latest_report_path: none",
        )
    if not open_targets:
        return (
            "closure_target_root_spec_id: none",
            "closure_target_open: none",
            "closure_target_blocked_by_lineage_work: none",
            "closure_target_latest_verdict_path: none",
            "closure_target_latest_report_path: none",
        )

    target = open_targets[0]
    return (
        f"closure_target_root_spec_id: {target.root_spec_id}",
        f"closure_target_open: {'true' if target.closure_open else 'false'}",
        (
            "closure_target_blocked_by_lineage_work: "
            f"{'true' if target.closure_blocked_by_lineage_work else 'false'}"
        ),
        f"closure_target_latest_verdict_path: {_value(target.latest_verdict_path)}",
        f"closure_target_latest_report_path: {_value(target.latest_report_path)}",
    )


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
    if outcome.compile_input_fingerprint is not None:
        typer.echo(f"compile_input.mode_id: {outcome.compile_input_fingerprint.mode_id}")
        typer.echo(
            "compile_input.config_fingerprint: "
            f"{outcome.compile_input_fingerprint.config_fingerprint}"
        )
        typer.echo(
            "compile_input.assets_fingerprint: "
            f"{outcome.compile_input_fingerprint.assets_fingerprint}"
        )
    for warning in diagnostics.warnings:
        typer.echo(f"warning: {warning}")
    for error in diagnostics.errors:
        typer.echo(f"error: {error}")
    return 0 if diagnostics.ok else 1


def _render_compile_show_lines(paths: WorkspacePaths, outcome: CompileOutcome) -> tuple[str, ...]:
    plan = outcome.active_plan
    if plan is None:
        return ()

    baseline_manifest = _load_baseline_manifest_safe(paths)
    learning_graph = getattr(plan, "learning_graph", None)
    plan_fingerprint = getattr(plan, "compile_input_fingerprint", outcome.compile_input_fingerprint)
    currentness_state = (
        "current"
        if outcome.compile_input_fingerprint is None
        or plan_fingerprint == outcome.compile_input_fingerprint
        else "stale"
    )
    lines = [
        f"compiled_plan_currentness: {currentness_state}",
        f"compiled_plan_id: {plan.compiled_plan_id}",
        f"execution_loop_id: {plan.execution_loop_id}",
        f"planning_loop_id: {plan.planning_loop_id}",
    ]
    if getattr(plan, "learning_loop_id", None) is not None:
        lines.append(f"learning_loop_id: {plan.learning_loop_id}")
    lines.extend(_render_baseline_manifest_lines(baseline_manifest))
    expected_fingerprint = outcome.compile_input_fingerprint or plan_fingerprint
    if expected_fingerprint is None:
        lines.extend(_render_compile_currentness_lines(None, None))
    else:
        lines.extend(
            _render_compile_currentness_lines(
                CompiledPlanCurrentness(
                    state=currentness_state,
                    expected_fingerprint=expected_fingerprint,
                    persisted_plan_id=plan.compiled_plan_id,
                    persisted_fingerprint=plan_fingerprint,
                ),
                None,
            )
        )
    for entry in plan.execution_graph.compiled_entries:
        lines.append(f"entry: execution.{entry.entry_key.value} -> {entry.node_id}")
    for entry in plan.planning_graph.compiled_entries:
        lines.append(f"entry: planning.{entry.entry_key.value} -> {entry.node_id}")
    if learning_graph is not None:
        for entry in learning_graph.compiled_entries:
            lines.append(f"entry: learning.{entry.entry_key.value} -> {entry.node_id}")
    completion_entry = plan.planning_graph.compiled_completion_entry
    if completion_entry is not None:
        lines.append(f"completion: {completion_entry.entry_key.value} -> {completion_entry.node_id}")

    completion_behavior = getattr(plan.planning_graph, "completion_behavior", None)
    if completion_behavior is not None:
        lines.extend(
            (
                f"completion_behavior.trigger: {completion_behavior.trigger}",
                f"completion_behavior.readiness_rule: {completion_behavior.readiness_rule}",
                f"completion_behavior.request_kind: {completion_behavior.request_kind}",
                f"completion_behavior.target_selector: {completion_behavior.target_selector}",
                f"completion_behavior.rubric_policy: {completion_behavior.rubric_policy}",
                f"completion_behavior.blocked_work_policy: {completion_behavior.blocked_work_policy}",
                "completion_behavior.skip_if_already_closed: "
                f"{'true' if completion_behavior.skip_if_already_closed else 'false'}",
                "completion_behavior.on_pass_terminal_state_id: "
                f"{completion_behavior.on_pass_terminal_state_id}",
                "completion_behavior.on_gap_terminal_state_id: "
                f"{completion_behavior.on_gap_terminal_state_id}",
                "completion_behavior.create_incident_on_gap: "
                f"{'true' if completion_behavior.create_incident_on_gap else 'false'}",
            )
        )
    graph_nodes = sorted(
        (
            *plan.execution_graph.nodes,
            *plan.planning_graph.nodes,
            *(learning_graph.nodes if learning_graph is not None else ()),
        ),
        key=lambda item: (item.plane.value, item.node_id),
    )
    for stage_plan in graph_nodes:
        stage_kind_id = getattr(stage_plan, "stage_kind_id", stage_plan.node_id)
        running_status_marker = getattr(stage_plan, "running_status_marker", "none")
        lines.extend(
            (
                f"stage: {stage_plan.plane.value}.{stage_plan.node_id}",
                f"stage_kind_id: {stage_kind_id}",
                f"running_status_marker: {running_status_marker}",
                f"entrypoint_path: {stage_plan.entrypoint_path}",
                f"entrypoint_contract_id: {stage_plan.entrypoint_contract_id or 'none'}",
                "required_skills: "
                f"{', '.join(stage_plan.required_skill_paths) if stage_plan.required_skill_paths else 'none'}",
                "attached_skills: "
                f"{', '.join(stage_plan.attached_skill_additions) if stage_plan.attached_skill_additions else 'none'}",
                f"runner_name: {stage_plan.runner_name or 'none'}",
                f"model_name: {stage_plan.model_name or 'none'}",
                f"timeout_seconds: {stage_plan.timeout_seconds}",
            )
        )
    return tuple(lines)


def _render_baseline_manifest_lines(manifest: BaselineManifest | None) -> tuple[str, ...]:
    if manifest is None:
        return (
            "baseline_manifest_id: none",
            "baseline_seed_package_version: none",
        )
    return (
        f"baseline_manifest_id: {manifest.manifest_id}",
        f"baseline_seed_package_version: {manifest.seed_package_version}",
    )


def _render_compile_currentness_lines(
    currentness: CompiledPlanCurrentness | None,
    error: str | None,
) -> tuple[str, ...]:
    if currentness is None:
        return (
            "compile_input.mode_id: none",
            "compile_input.config_fingerprint: none",
            "compile_input.assets_fingerprint: none",
            f"compile_plan_currentness_error: {error or 'none'}",
        )
    lines = (
        f"compile_input.mode_id: {currentness.expected_fingerprint.mode_id}",
        (
            "compile_input.config_fingerprint: "
            f"{currentness.expected_fingerprint.config_fingerprint}"
        ),
        (
            "compile_input.assets_fingerprint: "
            f"{currentness.expected_fingerprint.assets_fingerprint}"
        ),
    )
    if currentness.persisted_fingerprint is None:
        persisted = (
            "persisted_compile_input.mode_id: none",
            "persisted_compile_input.config_fingerprint: none",
            "persisted_compile_input.assets_fingerprint: none",
        )
    else:
        persisted = (
            f"persisted_compile_input.mode_id: {currentness.persisted_fingerprint.mode_id}",
            (
                "persisted_compile_input.config_fingerprint: "
                f"{currentness.persisted_fingerprint.config_fingerprint}"
            ),
            (
                "persisted_compile_input.assets_fingerprint: "
                f"{currentness.persisted_fingerprint.assets_fingerprint}"
            ),
        )
    return lines + persisted


def _compiled_plan_currentness_value(
    currentness: CompiledPlanCurrentness | None,
    error: str | None,
) -> str:
    if currentness is not None:
        return currentness.state
    if error is not None:
        return "unknown"
    return "missing"


def _load_baseline_manifest_safe(paths: WorkspacePaths) -> BaselineManifest | None:
    try:
        return load_baseline_manifest(paths)
    except Exception:
        return None


def _load_compile_currentness(
    paths: WorkspacePaths,
) -> tuple[CompiledPlanCurrentness | None, str | None]:
    try:
        config = load_runtime_config(paths.runtime_root / "millrace.toml")
        return (
            inspect_workspace_plan_currentness(
                paths,
                config=config,
                assets_root=paths.runtime_root,
            ),
            None,
        )
    except Exception as exc:
        return None, str(exc)


def _print_error(message: str) -> int:
    typer.echo(f"error: {message}")
    return 1


__all__ = [
    "_print_control_result",
    "_print_error",
    "_print_status",
    "_print_statuses",
    "_render_compile_diagnostics",
    "_render_compile_show_lines",
    "_render_config_show_lines",
    "_render_run_show_lines",
    "_render_runs_ls_lines",
    "_render_status_lines",
    "_resolve_run_artifact_path",
    "_run_once_exit_code",
    "_value",
]
