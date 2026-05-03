"""CLI for the optional Millrace Web sidecar."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from millrace_web.server import serve as serve_dashboard

app = typer.Typer(help="Serve the optional read-only Millrace Web dashboard.")


@app.callback()
def root() -> None:
    """Serve and inspect optional Millrace Web dashboard commands."""


@app.command()
def serve(
    workspace: Annotated[
        list[Path],
        typer.Option("--workspace", exists=True, file_okay=False, dir_okay=True, resolve_path=True),
    ],
    host: Annotated[str, typer.Option("--host")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", min=1, max=65535)] = 8765,
    view: Annotated[str, typer.Option("--view")] = "detail",
    poll_interval_seconds: Annotated[float, typer.Option("--poll-interval-seconds", min=0.1)] = 1.0,
) -> None:
    if view not in {"detail", "flow"}:
        raise typer.BadParameter("view must be 'detail' or 'flow'")
    serve_dashboard(
        workspaces=workspace,
        host=host,
        port=port,
        view=view,
        poll_interval_seconds=poll_interval_seconds,
    )


def main() -> None:
    app()


if __name__ == "__main__":
    main()
