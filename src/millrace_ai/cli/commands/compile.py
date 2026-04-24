"""Compile validation and inspection command group."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from millrace_ai.cli.formatting import _render_compile_diagnostics
from millrace_ai.cli.shared import ConfigOption, WorkspaceOption, _cli_api, _ensure_paths, _resolve_config_path

compile_app = typer.Typer(add_completion=False, no_args_is_help=True)


@compile_app.command("validate")
def compile_validate(
    workspace: WorkspaceOption = Path("."),
    mode: Annotated[str | None, typer.Option("--mode", help="Mode id to compile.")] = None,
    config_path: ConfigOption = None,
) -> None:
    paths = _ensure_paths(workspace)
    config = _cli_api().load_runtime_config(_resolve_config_path(paths, config_path))
    outcome = _cli_api().compile_and_persist_workspace_plan(
        paths,
        config=config,
        requested_mode_id=mode,
    )
    raise typer.Exit(code=_render_compile_diagnostics(outcome))


@compile_app.command("show")
def compile_show(
    workspace: WorkspaceOption = Path("."),
    mode: Annotated[str | None, typer.Option("--mode", help="Mode id to compile.")] = None,
    config_path: ConfigOption = None,
) -> None:
    paths = _ensure_paths(workspace)
    config = _cli_api().load_runtime_config(_resolve_config_path(paths, config_path))
    outcome = _cli_api().compile_and_persist_workspace_plan(
        paths,
        config=config,
        requested_mode_id=mode,
    )
    exit_code = _render_compile_diagnostics(outcome)

    if outcome.active_plan is not None:
        plan = outcome.active_plan
        typer.echo(f"compiled_plan_id: {plan.compiled_plan_id}")
        typer.echo(f"execution_loop_id: {plan.execution_loop_id}")
        typer.echo(f"planning_loop_id: {plan.planning_loop_id}")
        for entry in plan.execution_graph.compiled_entries:
            typer.echo(f"entry: execution.{entry.entry_key.value} -> {entry.node_id}")
        for entry in plan.planning_graph.compiled_entries:
            typer.echo(f"entry: planning.{entry.entry_key.value} -> {entry.node_id}")
        completion_entry = plan.planning_graph.compiled_completion_entry
        if completion_entry is not None:
            typer.echo(f"completion: {completion_entry.entry_key.value} -> {completion_entry.node_id}")

        completion_behavior = plan.planning_graph.completion_behavior
        if completion_behavior is not None:
            typer.echo(f"completion_behavior.trigger: {completion_behavior.trigger}")
            typer.echo(f"completion_behavior.readiness_rule: {completion_behavior.readiness_rule}")
            typer.echo(f"completion_behavior.request_kind: {completion_behavior.request_kind}")
            typer.echo(f"completion_behavior.target_selector: {completion_behavior.target_selector}")
            typer.echo(f"completion_behavior.rubric_policy: {completion_behavior.rubric_policy}")
            typer.echo(
                f"completion_behavior.blocked_work_policy: {completion_behavior.blocked_work_policy}"
            )
            typer.echo(
                "completion_behavior.skip_if_already_closed: "
                f"{'true' if completion_behavior.skip_if_already_closed else 'false'}"
            )
            typer.echo(
                "completion_behavior.on_pass_terminal_state_id: "
                f"{completion_behavior.on_pass_terminal_state_id}"
            )
            typer.echo(
                "completion_behavior.on_gap_terminal_state_id: "
                f"{completion_behavior.on_gap_terminal_state_id}"
            )
            typer.echo(
                "completion_behavior.create_incident_on_gap: "
                f"{'true' if completion_behavior.create_incident_on_gap else 'false'}"
            )
        graph_nodes = sorted(
            (*plan.execution_graph.nodes, *plan.planning_graph.nodes),
            key=lambda item: (item.plane.value, item.node_id),
        )
        for stage_plan in graph_nodes:
            typer.echo(f"stage: {stage_plan.plane.value}.{stage_plan.node_id}")
            typer.echo(f"entrypoint_path: {stage_plan.entrypoint_path}")
            typer.echo(f"entrypoint_contract_id: {stage_plan.entrypoint_contract_id or 'none'}")
            typer.echo(
                "required_skills: "
                f"{', '.join(stage_plan.required_skill_paths) if stage_plan.required_skill_paths else 'none'}"
            )
            typer.echo(
                "attached_skills: "
                f"{', '.join(stage_plan.attached_skill_additions) if stage_plan.attached_skill_additions else 'none'}"
            )
            typer.echo(f"runner_name: {stage_plan.runner_name or 'none'}")
            typer.echo(f"model_name: {stage_plan.model_name or 'none'}")
            typer.echo(f"timeout_seconds: {stage_plan.timeout_seconds}")

    raise typer.Exit(code=exit_code)
