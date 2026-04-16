"""Config inspection and reload command group."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError

from millrace_ai.cli.formatting import _print_control_result, _print_error, _render_compile_diagnostics, _render_config_show_lines
from millrace_ai.cli.shared import ConfigOption, WorkspaceOption, _cli_api, _ensure_paths, _resolve_config_path

config_app = typer.Typer(add_completion=False, no_args_is_help=True)


@config_app.command("show")
def config_show(
    workspace: WorkspaceOption = Path("."),
    config_path: ConfigOption = None,
) -> None:
    paths = _ensure_paths(workspace)
    resolved_config_path = _resolve_config_path(paths, config_path)
    try:
        config = _cli_api().load_runtime_config(resolved_config_path)
    except (OSError, ValidationError, ValueError) as exc:
        raise typer.Exit(code=_print_error(str(exc))) from exc
    for line in _render_config_show_lines(paths, config):
        typer.echo(line)


@config_app.command("validate")
def config_validate(
    workspace: WorkspaceOption = Path("."),
    config_path: ConfigOption = None,
    mode: Annotated[str | None, typer.Option("--mode", help="Optional mode id override.")] = None,
) -> None:
    paths = _ensure_paths(workspace)
    resolved_config_path = _resolve_config_path(paths, config_path)
    try:
        config = _cli_api().load_runtime_config(resolved_config_path)
        outcome = _cli_api().compile_and_persist_workspace_plan(
            paths,
            config=config,
            requested_mode_id=mode,
        )
    except (OSError, ValidationError, ValueError) as exc:
        raise typer.Exit(code=_print_error(str(exc))) from exc
    raise typer.Exit(code=_render_compile_diagnostics(outcome))


@config_app.command("reload")
def config_reload(workspace: WorkspaceOption = Path(".")) -> None:
    result = _cli_api().RuntimeControl(_ensure_paths(workspace)).reload_config()
    _print_control_result(result)
