"""Compile validation and inspection command group."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from millrace_ai.cli.formatting import _render_compile_diagnostics, _render_compile_show_lines
from millrace_ai.cli.shared import ConfigOption, WorkspaceOption, _cli_api, _require_paths, _resolve_config_path

compile_app = typer.Typer(add_completion=False, no_args_is_help=True)


@compile_app.command("validate")
def compile_validate(
    workspace: WorkspaceOption = Path("."),
    mode: Annotated[str | None, typer.Option("--mode", help="Mode id to compile.")] = None,
    config_path: ConfigOption = None,
) -> None:
    paths = _require_paths(workspace)
    config = _cli_api().load_runtime_config(_resolve_config_path(paths, config_path))
    outcome = _cli_api().compile_and_persist_workspace_plan(
        paths,
        config=config,
        requested_mode_id=mode,
        assets_root=paths.runtime_root,
    )
    raise typer.Exit(code=_render_compile_diagnostics(outcome))


@compile_app.command("show")
def compile_show(
    workspace: WorkspaceOption = Path("."),
    mode: Annotated[str | None, typer.Option("--mode", help="Mode id to compile.")] = None,
    config_path: ConfigOption = None,
) -> None:
    paths = _require_paths(workspace)
    config = _cli_api().load_runtime_config(_resolve_config_path(paths, config_path))
    outcome = _cli_api().compile_and_persist_workspace_plan(
        paths,
        config=config,
        requested_mode_id=mode,
        assets_root=paths.runtime_root,
    )
    exit_code = _render_compile_diagnostics(outcome)

    for line in _render_compile_show_lines(paths, outcome):
        typer.echo(line)

    raise typer.Exit(code=exit_code)
