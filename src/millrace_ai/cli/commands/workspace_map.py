"""Workspace map command group."""

from __future__ import annotations

from pathlib import Path

import typer

from millrace_ai.cli.shared import WorkspaceOption, _require_paths
from millrace_ai.workspace_map import refresh_workspace_map, show_workspace_map, validate_workspace_map

workspace_map_app = typer.Typer(add_completion=False, no_args_is_help=True)


@workspace_map_app.command("refresh")
def workspace_map_refresh(workspace: WorkspaceOption = Path(".")) -> None:
    paths = _require_paths(workspace)
    result = refresh_workspace_map(paths)
    typer.echo("workspace_map_refreshed: true")
    typer.echo(f"files: {result.file_count}")
    typer.echo(f"warnings: {result.warning_count}")
    typer.echo(f"fingerprint: {result.fingerprint}")
    for file_path in result.files_written:
        typer.echo(f"wrote: {file_path}")


@workspace_map_app.command("validate")
def workspace_map_validate(workspace: WorkspaceOption = Path(".")) -> None:
    paths = _require_paths(workspace)
    issues = validate_workspace_map(paths)
    typer.echo(f"ok: {'true' if not issues else 'false'}")
    typer.echo(f"issues: {len(issues)}")
    for issue in issues:
        typer.echo(f"{issue.code}: {issue.path} {issue.message}")
    raise typer.Exit(code=0 if not issues else 1)


@workspace_map_app.command("show")
def workspace_map_show(workspace: WorkspaceOption = Path(".")) -> None:
    paths = _require_paths(workspace)
    typer.echo(show_workspace_map(paths), nl=False)


__all__ = ["workspace_map_app"]
