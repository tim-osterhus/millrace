"""Pure CLI rendering and output helpers."""

from __future__ import annotations

from pathlib import Path

import typer

from millrace_ai.contracts import TokenUsage
from millrace_ai.contracts.graph_exports import CompiledStageGraphExport
from millrace_ai.contracts.run_trace import RunTraceGraph
from millrace_ai.control import ControlActionResult
from millrace_ai.run_inspection import InspectedRunSummary


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
        f"artifact_status: {summary.artifact_status}",
        f"runtime_outcome: {summary.runtime_outcome}",
        f"compiled_plan_id: {_value(summary.compiled_plan_id)}",
        f"mode_id: {_value(summary.mode_id)}",
        f"request_kind: {_value(summary.request_kind)}",
        f"closure_target_root_spec_id: {_value(summary.closure_target_root_spec_id)}",
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
                f"failure_class: {_value(stage_result.failure_class)}",
                f"failure_origin: {_value(stage_result.failure_origin)}",
                (
                    "request_context_profile_id: "
                    f"{_value(stage_result.request_context_profile_id)}"
                ),
                f"context_bundle_path: {_value(stage_result.context_bundle_path)}",
                f"context_render_plan_id: {_value(stage_result.context_render_plan_id)}",
                (
                    "rendered_prompt_context_path: "
                    f"{_value(stage_result.rendered_prompt_context_path)}"
                ),
                (
                    "runtime_effect_handler_id: "
                    f"{_value(stage_result.runtime_effect_handler_id)}"
                ),
                f"runtime_effect_decision: {_value(stage_result.runtime_effect_decision)}",
                (
                    "runtime_effect_failure_class: "
                    f"{_value(stage_result.runtime_effect_failure_class)}"
                ),
                (
                    "runtime_effect_failure_message: "
                    f"{_value(stage_result.runtime_effect_failure_message)}"
                ),
                (
                    "runtime_effect_mutation_phase: "
                    f"{_value(stage_result.runtime_effect_mutation_phase)}"
                ),
                (
                    "runtime_effect_failure_policy_id: "
                    f"{_value(stage_result.runtime_effect_failure_policy_id)}"
                ),
                (
                    "runtime_effect_recovery_action: "
                    f"{_value(stage_result.runtime_effect_recovery_action)}"
                ),
                (
                    "runtime_effect_source_lifecycle_plan_id: "
                    f"{_value(stage_result.runtime_effect_source_lifecycle_plan_id)}"
                ),
                (
                    "runtime_effect_source_lifecycle_action: "
                    f"{_value(stage_result.runtime_effect_source_lifecycle_action)}"
                ),
                f"runner_name: {_value(stage_result.runner_name)}",
                f"model_name: {_value(stage_result.model_name)}",
                f"thinking_level: {_value(stage_result.thinking_level)}",
                f"model_reasoning_effort: {_value(stage_result.model_reasoning_effort)}",
                f"model_assignment_alias_id: {_value(stage_result.model_assignment_alias_id)}",
                f"model_assignment_source: {_value(stage_result.model_assignment_source)}",
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
        for context_ref in stage_result.context_artifact_refs:
            lines.append(f"context_artifact_ref: {context_ref}")
        for path in stage_result.runtime_effect_created_paths:
            lines.append(f"runtime_effect_created_path: {path}")
        for summary in stage_result.capability_grant_summaries:
            lines.append(f"capability_grant: {summary}")
        for summary in stage_result.capability_support_summaries:
            lines.append(f"capability_support: {summary}")
    return tuple(lines)


def _render_compiled_graph_lines(
    graphs: tuple[CompiledStageGraphExport, ...],
) -> tuple[str, ...]:
    if not graphs:
        return ("compiled_graphs: none",)
    lines = [
        f"compiled_plan_id: {graphs[0].compiled_plan_id}",
        f"mode_id: {graphs[0].mode_id}",
        "planes: " + ", ".join(graph.plane.value for graph in graphs),
    ]
    for graph in graphs:
        lines.append("")
        lines.append(f"{graph.plane.value}:")
        if graph.runtime_failure_recovery is not None:
            recovery = graph.runtime_failure_recovery
            lines.append(
                "  runtime_failure_recovery: "
                f"default_repair_node_id={recovery.default_repair_node_id} "
                f"counter_name={recovery.counter_name} "
                f"threshold={recovery.threshold} "
                f"exhausted_terminal_state_id={_value(recovery.exhausted_terminal_state_id)}"
            )
        else:
            lines.append("  runtime_failure_recovery: none")
        for edge in graph.edges:
            target = (
                edge.target_node_id
                if edge.target_node_id is not None
                else f"terminal:{edge.terminal_state_id}"
            )
            lines.append(f"  {edge.source_node_id} --{edge.outcome}--> {target}")
    return tuple(lines)


def _render_run_trace_lines(trace: RunTraceGraph) -> tuple[str, ...]:
    lines = [
        f"run_id: {trace.run_id}",
        f"status: {trace.status}",
        f"compiled_plan_id: {_value(trace.compiled_plan_id)}",
        f"mode_id: {_value(trace.mode_id)}",
        f"request_kind: {_value(trace.request_kind)}",
        f"work_item_kind: {_value(trace.work_item_kind)}",
        f"work_item_id: {_value(trace.work_item_id)}",
        f"node_count: {len(trace.nodes)}",
        f"edge_count: {len(trace.edges)}",
    ]
    for note in trace.notes:
        lines.append(f"note: {note}")
    if not trace.edges:
        for node in trace.nodes:
            lines.append(f"{node.stage} {node.terminal_result}")
        return tuple(lines)
    nodes_by_trace_id = {node.trace_node_id: node for node in trace.nodes}
    for edge in trace.edges:
        source = nodes_by_trace_id.get(edge.source_trace_node_id)
        source_label = source.stage if source is not None else edge.source_trace_node_id
        target = (
            edge.target_trace_node_id
            or edge.target_node_id
            or f"terminal:{edge.terminal_state_id}"
        )
        lines.append(f"{source_label} {edge.outcome} -> {target}")
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
    "_render_compiled_graph_lines",
    "_render_run_show_lines",
    "_render_run_trace_lines",
    "_resolve_run_artifact_path",
    "_value",
]
