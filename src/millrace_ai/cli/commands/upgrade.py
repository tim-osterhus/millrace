"""Workspace baseline upgrade command."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from millrace_ai.cli.errors import _print_error
from millrace_ai.cli.shared import WorkspaceOption, _require_paths
from millrace_ai.workspace.baseline import apply_baseline_upgrade, preview_baseline_upgrade


def upgrade(
    workspace: WorkspaceOption = Path("."),
    apply: bool = typer.Option(False, "--apply", help="Apply managed baseline updates."),
    localize_removed: Annotated[
        list[str] | None,
        typer.Option(
            "--localize-removed",
            help="Preserve a removed managed asset as local workspace-owned content.",
        ),
    ] = None,
    localize_removed_from: Annotated[
        Path | None,
        typer.Option(
            "--localize-removed-from",
            help="Read newline-delimited removed managed asset paths to localize.",
            file_okay=True,
            dir_okay=False,
            resolve_path=True,
        ),
    ] = None,
) -> None:
    paths = _require_paths(workspace)
    localize_removed_paths = _collect_localize_removed_paths(
        localize_removed or (),
        localize_removed_from,
    )
    try:
        outcome = (
            apply_baseline_upgrade(paths, localize_removed_paths=localize_removed_paths)
            if apply
            else preview_baseline_upgrade(paths, localize_removed_paths=localize_removed_paths)
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


def _collect_localize_removed_paths(
    direct_paths: list[str] | tuple[str, ...],
    from_file: Path | None,
) -> tuple[str, ...]:
    values: list[str] = [path for path in direct_paths if path.strip()]
    if from_file is not None:
        try:
            lines = from_file.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise typer.Exit(code=_print_error(str(exc))) from exc
        values.extend(line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#"))
    return tuple(values)
