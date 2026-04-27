"""Workspace baseline upgrade command."""

from __future__ import annotations

from pathlib import Path

import typer

from millrace_ai.cli.errors import _print_error
from millrace_ai.cli.shared import WorkspaceOption, _require_paths
from millrace_ai.workspace.baseline import apply_baseline_upgrade, preview_baseline_upgrade


def upgrade(
    workspace: WorkspaceOption = Path("."),
    apply: bool = typer.Option(False, "--apply", help="Apply managed baseline updates."),
) -> None:
    paths = _require_paths(workspace)
    try:
        outcome = (
            apply_baseline_upgrade(paths)
            if apply
            else preview_baseline_upgrade(paths)
        )
    except ValueError as exc:
        raise typer.Exit(code=_print_error(str(exc))) from exc

    typer.echo(f"applied: {'true' if outcome.applied else 'false'}")
    typer.echo(f"baseline_manifest_id: {outcome.baseline_manifest_id}")
    typer.echo(f"candidate_manifest_id: {outcome.candidate_manifest_id}")
    if outcome.applied:
        typer.echo(f"result_manifest_id: {outcome.candidate_manifest_id}")
    for disposition, count in outcome.counts_by_disposition.items():
        typer.echo(f"{disposition.value}: {count}")
    for entry in outcome.entries:
        typer.echo(f"entry: {entry.relative_path} {entry.disposition.value}")
