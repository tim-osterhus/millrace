"""Status command group."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from millrace_ai.cli.formatting import _print_status, _print_statuses
from millrace_ai.cli.shared import WorkspaceOption, _ensure_paths

status_app = typer.Typer(add_completion=False, no_args_is_help=False)


@status_app.callback(invoke_without_command=True)
def status(
    ctx: typer.Context,
    workspace: WorkspaceOption = Path("."),
) -> None:
    if ctx.invoked_subcommand is None:
        _print_status(_ensure_paths(workspace))


@status_app.command("show")
def status_show(workspace: WorkspaceOption = Path(".")) -> None:
    _print_status(_ensure_paths(workspace))


@status_app.command("watch")
def status_watch(
    workspace: Annotated[
        list[Path] | None,
        typer.Option(
            "--workspace",
            help="Workspace root directory.",
            file_okay=False,
            dir_okay=True,
            writable=True,
            resolve_path=True,
        ),
    ] = None,
    max_updates: Annotated[
        int | None,
        typer.Option("--max-updates", min=1, help="Stop after this many status updates."),
    ] = None,
    interval_seconds: Annotated[
        float,
        typer.Option("--interval-seconds", min=0.0, help="Polling interval between status updates."),
    ] = 1.0,
) -> None:
    from millrace_ai.cli.shared import _cli_api

    workspace_values = workspace if workspace else [Path(".")]
    unique_paths = []
    seen_roots: set[Path] = set()
    for path in workspace_values:
        resolved = _ensure_paths(path)
        if resolved.root in seen_roots:
            continue
        seen_roots.add(resolved.root)
        unique_paths.append(resolved)

    paths_list = tuple(unique_paths)
    update_count = 0

    try:
        while True:
            _print_statuses(paths_list)
            update_count += 1
            if max_updates is not None and update_count >= max_updates:
                break
            typer.echo("")
            _cli_api().time.sleep(interval_seconds)
    except KeyboardInterrupt:
        typer.echo("interrupted: true")
