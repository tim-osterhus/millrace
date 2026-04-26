"""Workspace initialization command."""

from __future__ import annotations

from pathlib import Path

import typer

from millrace_ai.cli.shared import WorkspaceOption, _initialize_paths


def init_workspace(workspace: WorkspaceOption = Path(".")) -> None:
    paths = _initialize_paths(workspace)
    typer.echo(f"workspace: {paths.root}")
    typer.echo("initialized: true")
