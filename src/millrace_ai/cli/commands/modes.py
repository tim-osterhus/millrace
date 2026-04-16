"""Mode inspection command group."""

from __future__ import annotations

from typing import Annotated

import typer

from millrace_ai.cli.shared import _cli_api

modes_app = typer.Typer(add_completion=False, no_args_is_help=True)


@modes_app.command("list")
def list_modes() -> None:
    for mode_id in sorted(_cli_api().BUILTIN_MODE_PATHS):
        mode_definition = _cli_api().load_builtin_mode_definition(mode_id)
        typer.echo(
            f"{mode_definition.mode_id}: execution_loop={mode_definition.execution_loop_id} "
            f"planning_loop={mode_definition.planning_loop_id}"
        )


@modes_app.command("show")
def show_mode(mode_id: Annotated[str, typer.Argument(help="Mode ID to inspect.")]) -> None:
    mode_definition = _cli_api().load_builtin_mode_definition(mode_id)
    typer.echo(f"mode_id: {mode_definition.mode_id}")
    typer.echo(f"execution_loop_id: {mode_definition.execution_loop_id}")
    typer.echo(f"planning_loop_id: {mode_definition.planning_loop_id}")
