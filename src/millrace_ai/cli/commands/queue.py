"""Queue inspection and enqueue command group."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError

from millrace_ai.cli.formatting import _print_control_result, _print_error
from millrace_ai.cli.shared import (
    WorkspaceOption,
    _cli_api,
    _load_spec_document,
    _load_task_document,
    _queue_lookup,
    _require_paths,
    _validate_work_item_id,
)
from millrace_ai.contracts import IncidentDocument, SpecDocument, TaskDocument
from millrace_ai.work_documents import parse_work_document_as

queue_app = typer.Typer(add_completion=False, no_args_is_help=True)


@queue_app.command("ls")
def queue_ls(workspace: WorkspaceOption = Path(".")) -> None:
    paths = _require_paths(workspace)
    execution_queue_depth = len(tuple(paths.tasks_queue_dir.glob("*.md")))
    planning_queue_depth = len(tuple(paths.specs_queue_dir.glob("*.md"))) + len(
        tuple(paths.incidents_incoming_dir.glob("*.md"))
    )
    execution_active = len(tuple(paths.tasks_active_dir.glob("*.md")))
    planning_active = len(tuple(paths.specs_active_dir.glob("*.md"))) + len(
        tuple(paths.incidents_active_dir.glob("*.md"))
    )

    typer.echo(f"execution_queue_depth: {execution_queue_depth}")
    typer.echo(f"planning_queue_depth: {planning_queue_depth}")
    typer.echo(f"execution_active: {execution_active}")
    typer.echo(f"planning_active: {planning_active}")


@queue_app.command("show")
def queue_show(
    work_item_id: Annotated[str, typer.Argument(help="Task/spec/incident ID to inspect.")],
    workspace: WorkspaceOption = Path("."),
) -> None:
    paths = _require_paths(workspace)
    try:
        validated_work_item_id = _validate_work_item_id(work_item_id)
    except ValueError as exc:
        raise typer.Exit(code=_print_error(f"invalid work item id: {exc}")) from exc

    located = _queue_lookup(paths, work_item_id=validated_work_item_id)
    if located is None:
        raise typer.Exit(code=_print_error(f"work item not found: {validated_work_item_id}"))
    work_item_kind, state, path = located

    document: TaskDocument | SpecDocument | IncidentDocument
    if work_item_kind == "task":
        document = parse_work_document_as(
            path.read_text(encoding="utf-8"),
            model=TaskDocument,
            path=path,
        )
    elif work_item_kind == "spec":
        document = parse_work_document_as(
            path.read_text(encoding="utf-8"),
            model=SpecDocument,
            path=path,
        )
    else:
        document = parse_work_document_as(
            path.read_text(encoding="utf-8"),
            model=IncidentDocument,
            path=path,
        )

    typer.echo(f"work_item_id: {validated_work_item_id}")
    typer.echo(f"work_item_kind: {work_item_kind}")
    typer.echo(f"work_item_state: {state}")
    typer.echo(f"path: {path}")
    typer.echo(f"title: {getattr(document, 'title', 'unknown')}")


@queue_app.command("add-task")
def queue_add_task(
    task_path: Annotated[Path, typer.Argument(exists=True, file_okay=True, dir_okay=False, resolve_path=True)],
    workspace: WorkspaceOption = Path("."),
) -> None:
    paths = _require_paths(workspace)
    try:
        document = _load_task_document(task_path)
        result = _cli_api().RuntimeControl(paths).add_task(document)
    except (OSError, ValidationError, ValueError) as exc:
        raise typer.Exit(code=_print_error(f"failed to add task: {exc}")) from exc
    if result.mode == "mailbox":
        _print_control_result(result)
        return
    if result.artifact_path is None:
        raise typer.Exit(code=_print_error("failed to add task: missing artifact path"))
    typer.echo(f"enqueued_task: {result.artifact_path}")


@queue_app.command("add-spec")
def queue_add_spec(
    spec_path: Annotated[Path, typer.Argument(exists=True, file_okay=True, dir_okay=False, resolve_path=True)],
    workspace: WorkspaceOption = Path("."),
) -> None:
    paths = _require_paths(workspace)
    try:
        document = _load_spec_document(spec_path)
        result = _cli_api().RuntimeControl(paths).add_spec(document)
    except (OSError, ValidationError, ValueError) as exc:
        raise typer.Exit(code=_print_error(f"failed to add spec: {exc}")) from exc
    if result.mode == "mailbox":
        _print_control_result(result)
        return
    if result.artifact_path is None:
        raise typer.Exit(code=_print_error("failed to add spec: missing artifact path"))
    typer.echo(f"enqueued_spec: {result.artifact_path}")


@queue_app.command("add-idea")
def queue_add_idea(
    idea_path: Annotated[Path, typer.Argument(exists=True, file_okay=True, dir_okay=False, resolve_path=True)],
    workspace: WorkspaceOption = Path("."),
) -> None:
    paths = _require_paths(workspace)
    try:
        markdown = idea_path.read_text(encoding="utf-8")
        result = _cli_api().RuntimeControl(paths).add_idea_markdown(
            source_name=idea_path.name,
            markdown=markdown,
        )
    except (OSError, ValueError) as exc:
        raise typer.Exit(code=_print_error(f"failed to add idea: {exc}")) from exc
    if result.mode == "mailbox":
        _print_control_result(result)
        return
    if result.artifact_path is None:
        raise typer.Exit(code=_print_error("failed to add idea: missing artifact path"))
    typer.echo(f"enqueued_idea: {result.artifact_path}")


def add_task(
    task_path: Annotated[Path, typer.Argument(exists=True, file_okay=True, dir_okay=False, resolve_path=True)],
    workspace: WorkspaceOption = Path("."),
) -> None:
    queue_add_task(task_path=task_path, workspace=workspace)


def add_spec(
    spec_path: Annotated[Path, typer.Argument(exists=True, file_okay=True, dir_okay=False, resolve_path=True)],
    workspace: WorkspaceOption = Path("."),
) -> None:
    queue_add_spec(spec_path=spec_path, workspace=workspace)
