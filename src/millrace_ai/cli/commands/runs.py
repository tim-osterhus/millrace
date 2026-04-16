"""Run inspection command group."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from millrace_ai.cli.formatting import (
    _print_error,
    _render_run_show_lines,
    _render_runs_ls_lines,
    _resolve_run_artifact_path,
)
from millrace_ai.cli.shared import WorkspaceOption, _cli_api, _ensure_paths

runs_app = typer.Typer(add_completion=False, no_args_is_help=True)


@runs_app.command("ls")
def runs_ls(workspace: WorkspaceOption = Path(".")) -> None:
    for line in _render_runs_ls_lines(_ensure_paths(workspace)):
        typer.echo(line)


@runs_app.command("show")
def runs_show(
    run_id: Annotated[str, typer.Argument(help="Run ID to inspect.")],
    workspace: WorkspaceOption = Path("."),
) -> None:
    summary = _cli_api().inspect_run_id(_ensure_paths(workspace), run_id)
    if summary is None:
        raise typer.Exit(code=_print_error(f"run not found: {run_id}"))
    for line in _render_run_show_lines(summary):
        typer.echo(line)


@runs_app.command("tail")
def runs_tail(
    run_id: Annotated[str, typer.Argument(help="Run ID to tail.")],
    workspace: WorkspaceOption = Path("."),
) -> None:
    summary = _cli_api().inspect_run_id(_ensure_paths(workspace), run_id)
    if summary is None:
        raise typer.Exit(code=_print_error(f"run not found: {run_id}"))
    selected_artifact = _cli_api().select_primary_run_artifact(summary)
    if selected_artifact is None:
        raise typer.Exit(code=_print_error(f"no tailable artifact found for run: {run_id}"))

    artifact_path = _resolve_run_artifact_path(summary.run_dir, selected_artifact)
    try:
        typer.echo(artifact_path.read_text(encoding="utf-8"), nl=False)
    except OSError as exc:
        raise typer.Exit(code=_print_error(str(exc))) from exc
