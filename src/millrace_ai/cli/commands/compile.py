"""Compile validation and inspection command group."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from millrace_ai.cli.compile_view import _render_compile_diagnostics, _render_compile_show_lines
from millrace_ai.cli.errors import _print_error
from millrace_ai.cli.formatting import _render_compiled_graph_lines
from millrace_ai.cli.shared import ConfigOption, WorkspaceOption, _cli_api, _require_paths, _resolve_config_path
from millrace_ai.compilation.graph_exports import (
    export_compiled_stage_graph,
    export_compiled_stage_graphs,
)
from millrace_ai.contracts import Plane

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


@compile_app.command("graph")
def compile_graph(
    workspace: WorkspaceOption = Path("."),
    mode: Annotated[str | None, typer.Option("--mode", help="Mode id to compile.")] = None,
    config_path: ConfigOption = None,
    plane: Annotated[str | None, typer.Option("--plane", help="Plane to export.")] = None,
    output_format: Annotated[
        str,
        typer.Option("--format", help="Output format: text or json."),
    ] = "text",
    output: Annotated[
        Path | None,
        typer.Option("--output", help="Optional output file path."),
    ] = None,
) -> None:
    paths = _require_paths(workspace)
    config = _cli_api().load_runtime_config(_resolve_config_path(paths, config_path))
    outcome = _cli_api().compile_and_persist_workspace_plan(
        paths,
        config=config,
        requested_mode_id=mode,
        assets_root=paths.runtime_root,
    )
    if outcome.active_plan is None:
        raise typer.Exit(code=_render_compile_diagnostics(outcome))

    try:
        selected_graphs = (
            (export_compiled_stage_graph(outcome.active_plan, Plane(plane)),)
            if plane is not None
            else export_compiled_stage_graphs(outcome.active_plan)
        )
    except ValueError as exc:
        raise typer.Exit(code=_print_error(str(exc))) from exc

    if output_format not in {"text", "json"}:
        raise typer.Exit(code=_print_error("--format must be text or json"))
    if output_format == "json":
        rendered = json.dumps(
            [graph.model_dump(mode="json") for graph in selected_graphs],
            indent=2,
        )
    else:
        rendered = "\n".join(_render_compiled_graph_lines(selected_graphs))
    if output is not None:
        output.write_text(rendered + "\n", encoding="utf-8")
        return
    typer.echo(rendered)
