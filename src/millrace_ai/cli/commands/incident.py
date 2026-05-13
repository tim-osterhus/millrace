"""Incident operator intervention command group."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError

from millrace_ai.cli.errors import _print_error
from millrace_ai.cli.formatting import _print_control_result
from millrace_ai.cli.shared import WorkspaceOption, _cli_api, _require_paths, _validate_work_item_id
from millrace_ai.errors import ControlRoutingError, QueueStateError

incident_app = typer.Typer(add_completion=False, no_args_is_help=True)


@incident_app.command("resolve")
def incident_resolve(
    incident_id: Annotated[str, typer.Argument(help="Incident ID to mark operator-resolved.")],
    workspace: WorkspaceOption = Path("."),
    reason: Annotated[
        str,
        typer.Option("--reason", help="Audit reason for resolving the incident."),
    ] = "",
) -> None:
    paths = _require_paths(workspace)
    try:
        validated_incident_id = _validate_work_item_id(incident_id)
        result = _cli_api().RuntimeControl(paths).resolve_incident(
            incident_id=validated_incident_id,
            reason=reason,
        )
    except (OSError, ControlRoutingError, QueueStateError, ValidationError, ValueError) as exc:
        raise typer.Exit(code=_print_error(f"failed to resolve incident: {exc}")) from exc
    _print_control_result(result)


@incident_app.command("cancel")
def incident_cancel(
    incident_id: Annotated[str, typer.Argument(help="Incident ID to cancel without marking resolved.")],
    workspace: WorkspaceOption = Path("."),
    reason: Annotated[
        str,
        typer.Option("--reason", help="Audit reason for cancelling the incident."),
    ] = "",
) -> None:
    paths = _require_paths(workspace)
    try:
        validated_incident_id = _validate_work_item_id(incident_id)
        result = _cli_api().RuntimeControl(paths).cancel_incident(
            incident_id=validated_incident_id,
            reason=reason,
        )
    except (OSError, ControlRoutingError, QueueStateError, ValidationError, ValueError) as exc:
        raise typer.Exit(code=_print_error(f"failed to cancel incident: {exc}")) from exc
    _print_control_result(result)


@incident_app.command("archive-invalid")
def incident_archive_invalid(
    filename: Annotated[str, typer.Argument(help="Invalid incoming incident filename to archive.")],
    workspace: WorkspaceOption = Path("."),
    reason: Annotated[
        str,
        typer.Option("--reason", help="Audit reason for archiving the invalid incident artifact."),
    ] = "",
) -> None:
    paths = _require_paths(workspace)
    try:
        result = _cli_api().RuntimeControl(paths).archive_invalid_incident(
            filename=filename,
            reason=reason,
        )
    except (OSError, ControlRoutingError, QueueStateError, ValidationError, ValueError) as exc:
        raise typer.Exit(code=_print_error(f"failed to archive invalid incident artifact: {exc}")) from exc
    _print_control_result(result)


__all__ = ["incident_app", "incident_archive_invalid", "incident_cancel", "incident_resolve"]
