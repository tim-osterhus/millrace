"""Runtime control command group and root aliases."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from millrace_ai.cli.formatting import _print_control_result
from millrace_ai.cli.shared import WorkspaceOption, _cli_api, _require_paths

control_app = typer.Typer(add_completion=False, no_args_is_help=True)


@control_app.command("pause")
def control_pause(workspace: WorkspaceOption = Path(".")) -> None:
    result = _cli_api().RuntimeControl(_require_paths(workspace)).pause_runtime()
    _print_control_result(result)


@control_app.command("resume")
def control_resume(workspace: WorkspaceOption = Path(".")) -> None:
    result = _cli_api().RuntimeControl(_require_paths(workspace)).resume_runtime()
    _print_control_result(result)


@control_app.command("stop")
def control_stop(workspace: WorkspaceOption = Path(".")) -> None:
    result = _cli_api().RuntimeControl(_require_paths(workspace)).stop_runtime()
    _print_control_result(result)


@control_app.command("retry-active")
def control_retry_active(
    workspace: WorkspaceOption = Path("."),
    reason: Annotated[str, typer.Option("--reason", help="Reason attached to retry action.")] = "operator requested retry",
) -> None:
    result = _cli_api().RuntimeControl(_require_paths(workspace)).retry_active(reason=reason)
    _print_control_result(result)


@control_app.command("clear-stale-state")
def control_clear_stale_state(
    workspace: WorkspaceOption = Path("."),
    reason: Annotated[
        str,
        typer.Option("--reason", help="Reason attached to stale-state clear action."),
    ] = "operator requested stale-state clear",
) -> None:
    result = _cli_api().RuntimeControl(_require_paths(workspace)).clear_stale_state(reason=reason)
    _print_control_result(result)


@control_app.command("reload-config")
def control_reload_config(workspace: WorkspaceOption = Path(".")) -> None:
    result = _cli_api().RuntimeControl(_require_paths(workspace)).reload_config()
    _print_control_result(result)


def pause(workspace: WorkspaceOption = Path(".")) -> None:
    control_pause(workspace=workspace)


def resume(workspace: WorkspaceOption = Path(".")) -> None:
    control_resume(workspace=workspace)


def stop(workspace: WorkspaceOption = Path(".")) -> None:
    control_stop(workspace=workspace)


def retry_active(
    workspace: WorkspaceOption = Path("."),
    reason: Annotated[str, typer.Option("--reason", help="Reason attached to retry action.")] = "operator requested retry",
) -> None:
    control_retry_active(workspace=workspace, reason=reason)


def clear_stale_state(
    workspace: WorkspaceOption = Path("."),
    reason: Annotated[
        str,
        typer.Option("--reason", help="Reason attached to stale-state clear action."),
    ] = "operator requested stale-state clear",
) -> None:
    control_clear_stale_state(workspace=workspace, reason=reason)


def reload_config(workspace: WorkspaceOption = Path(".")) -> None:
    control_reload_config(workspace=workspace)
