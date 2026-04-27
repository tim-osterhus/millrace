"""CLI error rendering helpers."""

from __future__ import annotations

import typer


def _print_error(message: str) -> int:
    typer.echo(f"error: {message}")
    return 1


__all__ = ["_print_error"]
