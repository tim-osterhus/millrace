"""Compatibility facade for CLI status output."""

from __future__ import annotations

import json
from typing import Any, Sequence

import typer

from millrace_ai.paths import WorkspacePaths

from .status.collection import collect_status_view_model
from .status.rendering import render_status_lines, status_payload


def _render_status_lines(paths: WorkspacePaths) -> tuple[str, ...]:
    return render_status_lines(collect_status_view_model(paths))


def _status_payload(paths: WorkspacePaths) -> dict[str, Any]:
    return status_payload(collect_status_view_model(paths))


def _print_status(paths: WorkspacePaths) -> None:
    for line in _render_status_lines(paths):
        typer.echo(line)


def _print_status_json(paths: WorkspacePaths) -> None:
    typer.echo(json.dumps(_status_payload(paths), indent=2, sort_keys=True))


def _print_statuses(paths_list: Sequence[WorkspacePaths]) -> None:
    for index, paths in enumerate(paths_list):
        if index > 0:
            typer.echo("")
        _print_status(paths)


__all__ = [
    "_print_status",
    "_print_status_json",
    "_print_statuses",
    "_render_status_lines",
    "_status_payload",
]
