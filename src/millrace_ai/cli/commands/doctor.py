"""Workspace doctor command."""

from __future__ import annotations

from pathlib import Path

import typer

from millrace_ai.cli.shared import WorkspaceOption, _cli_api, _ensure_paths


def doctor(workspace: WorkspaceOption = Path(".")) -> None:
    report = _cli_api().run_workspace_doctor(_ensure_paths(workspace))
    typer.echo(f"ok: {'true' if report.ok else 'false'}")
    typer.echo(f"errors: {len(report.errors)}")
    typer.echo(f"warnings: {len(report.warnings)}")
    for issue in report.errors:
        location = str(issue.path) if issue.path is not None else "none"
        typer.echo(f"error: {issue.code} {location} {issue.message}")
    for warning in report.warnings:
        location = str(warning.path) if warning.path is not None else "none"
        typer.echo(f"warning: {warning.code} {location} {warning.message}")
    raise typer.Exit(code=0 if report.ok else 1)
