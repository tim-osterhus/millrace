"""Planning command group."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from millrace_ai.cli.formatting import _print_control_result
from millrace_ai.cli.shared import WorkspaceOption, _cli_api, _ensure_paths

planning_app = typer.Typer(add_completion=False, no_args_is_help=True)


@planning_app.command("retry-active")
def planning_retry_active(
    workspace: WorkspaceOption = Path("."),
    reason: Annotated[
        str,
        typer.Option("--reason", help="Reason attached to planning retry action."),
    ] = "operator requested planning retry",
) -> None:
    result = _cli_api().RuntimeControl(_ensure_paths(workspace)).retry_active_planning(reason=reason)
    _print_control_result(result)
