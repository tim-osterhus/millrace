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
        for stage_plan in sorted(plan.stage_plans, key=lambda item: (item.plane.value, item.stage.value)):
            typer.echo(f"stage: {stage_plan.plane.value}.{stage_plan.stage.value}")
            typer.echo(f"entrypoint_path: {stage_plan.entrypoint_path}")
            typer.echo(f"required_skills: {', '.join(stage_plan.required_skills) if stage_plan.required_skills else 'none'}")
            typer.echo(
                "attached_skills: "
                f"{', '.join(stage_plan.attached_skill_additions) if stage_plan.attached_skill_additions else 'none'}"
            )

    raise typer.Exit(code=exit_code)
