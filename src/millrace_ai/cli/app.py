"""CLI app assembly."""

from __future__ import annotations

import typer

import millrace_ai
from millrace_ai.cli.commands.compile import compile_app
from millrace_ai.cli.commands.config import config_app
from millrace_ai.cli.commands.control import (
    clear_stale_state,
    control_app,
    pause,
    reload_config,
    resume,
    retry_active,
    stop,
)
from millrace_ai.cli.commands.doctor import doctor
from millrace_ai.cli.commands.init import init_workspace
from millrace_ai.cli.commands.modes import modes_app
from millrace_ai.cli.commands.planning import planning_app
from millrace_ai.cli.commands.queue import add_spec, add_task, queue_add_idea, queue_app
from millrace_ai.cli.commands.run import run_app
from millrace_ai.cli.commands.runs import runs_app
from millrace_ai.cli.commands.skills import skills_app
from millrace_ai.cli.commands.status import status_app
from millrace_ai.cli.commands.upgrade import upgrade

app = typer.Typer(add_completion=False, no_args_is_help=False)


@app.callback(invoke_without_command=True)
def root(
    ctx: typer.Context,
    version: bool = typer.Option(
        False,
        "--version",
        help="Print the Millrace package version and exit.",
    ),
) -> None:
    if version:
        typer.echo(f"millrace {millrace_ai.__version__}")
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        typer.echo("Missing command")
        raise typer.Exit(code=2)

app.add_typer(run_app, name="run")
app.add_typer(queue_app, name="queue")
app.add_typer(control_app, name="control")
app.add_typer(status_app, name="status")
app.add_typer(runs_app, name="runs")
app.add_typer(planning_app, name="planning")
app.add_typer(config_app, name="config")
app.add_typer(modes_app, name="modes")
app.add_typer(compile_app, name="compile")
app.add_typer(skills_app, name="skills")

app.command("add-task")(add_task)
app.command("add-spec")(add_spec)
app.command("add-idea")(queue_add_idea)
app.command("init")(init_workspace)
app.command("pause")(pause)
app.command("resume")(resume)
app.command("stop")(stop)
app.command("retry-active")(retry_active)
app.command("clear-stale-state")(clear_stale_state)
app.command("reload-config")(reload_config)
app.command("doctor")(doctor)
app.command("upgrade")(upgrade)


@app.command("version")
def version() -> None:
    typer.echo(f"millrace {millrace_ai.__version__}")

__all__ = ["app"]
