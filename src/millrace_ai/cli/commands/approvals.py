"""Execution capability approval commands."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from millrace_ai.cli.errors import _print_error
from millrace_ai.cli.formatting import _print_control_result
from millrace_ai.cli.shared import WorkspaceOption, _require_paths
from millrace_ai.control import RuntimeControl
from millrace_ai.runtime.approvals import list_execution_capability_approvals

approvals_app = typer.Typer(add_completion=False, no_args_is_help=True)


@approvals_app.command("ls")
def approvals_ls(workspace: WorkspaceOption = Path(".")) -> None:
    listing = list_execution_capability_approvals(_require_paths(workspace))
    for approval in (*listing.pending, *listing.resolved):
        typer.echo(f"approval_id: {approval.approval_id}")
        typer.echo(f"status: {approval.status}")
        typer.echo(f"capability_id: {approval.capability_id}")
        typer.echo(f"grant_id: {approval.grant_id}")
        typer.echo(f"run_id: {approval.run_id}")
        typer.echo(f"work_item_id: {approval.work_item_id or 'none'}")
        typer.echo("")


@approvals_app.command("show")
def approvals_show(
    approval_id: Annotated[str, typer.Argument(help="Approval ID to inspect.")],
    workspace: WorkspaceOption = Path("."),
) -> None:
    listing = list_execution_capability_approvals(_require_paths(workspace))
    approval = next(
        (
            candidate
            for candidate in (*listing.pending, *listing.resolved)
            if candidate.approval_id == approval_id
        ),
        None,
    )
    if approval is None:
        raise typer.Exit(code=_print_error(f"approval not found: {approval_id}"))
    typer.echo(approval.model_dump_json(indent=2))


@approvals_app.command("approve")
def approvals_approve(
    approval_id: Annotated[str, typer.Argument(help="Approval ID to approve.")],
    workspace: WorkspaceOption = Path("."),
    reason: Annotated[str, typer.Option("--reason", help="Operator approval reason.")] = "approved",
) -> None:
    result = RuntimeControl(_require_paths(workspace)).approve_execution_capability(
        approval_id=approval_id,
        reason=reason,
    )
    _print_control_result(result)


@approvals_app.command("deny")
def approvals_deny(
    approval_id: Annotated[str, typer.Argument(help="Approval ID to deny.")],
    workspace: WorkspaceOption = Path("."),
    reason: Annotated[str, typer.Option("--reason", help="Operator denial reason.")] = "denied",
) -> None:
    result = RuntimeControl(_require_paths(workspace)).deny_execution_capability(
        approval_id=approval_id,
        reason=reason,
    )
    _print_control_result(result)


__all__ = ["approvals_app"]
