"""Model alias management command group."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError

from millrace_ai.cli.errors import _print_error
from millrace_ai.cli.formatting import _print_control_result
from millrace_ai.cli.shared import WorkspaceOption, _cli_api, _ensure_paths
from millrace_ai.config.toml_editing import (
    clear_model_assignment_default,
    clear_model_assignment_loop,
    clear_model_assignment_stage,
    remove_model_alias,
    set_model_alias,
    set_model_assignment_default,
    set_model_assignment_loop,
    set_model_assignment_stage,
)

model_aliases_app = typer.Typer(add_completion=False, no_args_is_help=True)

_SAFE_ALIAS_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")
_SAFE_VALUE_PATTERN = re.compile(r"^[A-Za-z0-9._:/+-]+$")


@model_aliases_app.command("list")
def model_aliases_list(workspace: WorkspaceOption = Path(".")) -> None:
    paths = _ensure_paths(workspace)
    try:
        config = _cli_api().load_runtime_config(paths.runtime_root / "millrace.toml")
    except (OSError, ValidationError, ValueError) as exc:
        raise typer.Exit(code=_print_error(str(exc))) from exc
    for alias_id in sorted(config.model_aliases):
        alias = config.model_aliases[alias_id]
        typer.echo(
            f"{alias_id}: model={alias.model or 'none'} "
            f"thinking_level={alias.thinking_level or 'none'}"
        )
    typer.echo(
        "assignment: "
        f"enabled={'true' if config.model_assignment.enabled else 'false'} "
        f"default_alias={config.model_assignment.default_alias}"
    )
    for loop_id in sorted(config.model_assignment.by_loop):
        typer.echo(f"by_loop.{loop_id}: {config.model_assignment.by_loop[loop_id]}")
    for stage in sorted(config.model_assignment.by_stage):
        typer.echo(f"by_stage.{stage}: {config.model_assignment.by_stage[stage]}")


@model_aliases_app.command("show")
def model_aliases_show(
    alias_id: Annotated[str, typer.Argument(help="Model alias id.")],
    workspace: WorkspaceOption = Path("."),
) -> None:
    paths = _ensure_paths(workspace)
    try:
        config = _cli_api().load_runtime_config(paths.runtime_root / "millrace.toml")
    except (OSError, ValidationError, ValueError) as exc:
        raise typer.Exit(code=_print_error(str(exc))) from exc
    alias = config.model_aliases.get(alias_id)
    if alias is None:
        raise typer.Exit(code=_print_error(f"Unknown model alias: {alias_id}"))
    typer.echo(f"alias: {alias_id}")
    typer.echo(f"model: {alias.model or 'none'}")
    typer.echo(f"thinking_level: {alias.thinking_level or 'none'}")


@model_aliases_app.command("set")
def model_aliases_set(
    alias_id: Annotated[str, typer.Argument(help="Model alias id.")],
    model: Annotated[str, typer.Option("--model", help="Model id assigned by this alias.")],
    thinking_level: Annotated[
        str,
        typer.Option("--thinking-level", help="Thinking level assigned by this alias."),
    ],
    workspace: WorkspaceOption = Path("."),
    reload_config: Annotated[
        bool,
        typer.Option("--reload/--no-reload", help="Request a config reload after writing TOML."),
    ] = True,
) -> None:
    _validate_alias_id(alias_id)
    _validate_alias_value("model", model)
    _validate_alias_value("thinking_level", thinking_level)
    paths = _ensure_paths(workspace)
    set_model_alias(
        paths.runtime_root / "millrace.toml",
        alias_id=alias_id,
        model=model,
        thinking_level=thinking_level,
    )
    _echo_mutated_or_reload(paths.root, reload_config=reload_config)


@model_aliases_app.command("remove")
def model_aliases_remove(
    alias_id: Annotated[str, typer.Argument(help="Model alias id.")],
    workspace: WorkspaceOption = Path("."),
    reload_config: Annotated[
        bool,
        typer.Option("--reload/--no-reload", help="Request a config reload after writing TOML."),
    ] = True,
) -> None:
    _validate_alias_id(alias_id)
    paths = _ensure_paths(workspace)
    remove_model_alias(paths.runtime_root / "millrace.toml", alias_id=alias_id)
    _echo_mutated_or_reload(paths.root, reload_config=reload_config)


@model_aliases_app.command("assign-global")
def model_aliases_assign_global(
    alias_id: Annotated[str, typer.Argument(help="Default model alias id.")],
    workspace: WorkspaceOption = Path("."),
    reload_config: Annotated[
        bool,
        typer.Option("--reload/--no-reload", help="Request a config reload after writing TOML."),
    ] = True,
) -> None:
    _validate_alias_id(alias_id)
    paths = _ensure_paths(workspace)
    set_model_assignment_default(paths.runtime_root / "millrace.toml", alias_id=alias_id)
    _echo_mutated_or_reload(paths.root, reload_config=reload_config)


@model_aliases_app.command("assign-loop")
def model_aliases_assign_loop(
    loop_id: Annotated[str, typer.Argument(help="Graph loop id.")],
    alias_id: Annotated[str, typer.Argument(help="Model alias id.")],
    workspace: WorkspaceOption = Path("."),
    reload_config: Annotated[
        bool,
        typer.Option("--reload/--no-reload", help="Request a config reload after writing TOML."),
    ] = True,
) -> None:
    _validate_alias_id(alias_id)
    paths = _ensure_paths(workspace)
    set_model_assignment_loop(paths.runtime_root / "millrace.toml", loop_id=loop_id, alias_id=alias_id)
    _echo_mutated_or_reload(paths.root, reload_config=reload_config)


@model_aliases_app.command("assign-stage")
def model_aliases_assign_stage(
    stage: Annotated[str, typer.Argument(help="Stage key or stage kind id.")],
    alias_id: Annotated[str, typer.Argument(help="Model alias id.")],
    workspace: WorkspaceOption = Path("."),
    reload_config: Annotated[
        bool,
        typer.Option("--reload/--no-reload", help="Request a config reload after writing TOML."),
    ] = True,
) -> None:
    _validate_alias_id(alias_id)
    paths = _ensure_paths(workspace)
    set_model_assignment_stage(paths.runtime_root / "millrace.toml", stage=stage, alias_id=alias_id)
    _echo_mutated_or_reload(paths.root, reload_config=reload_config)


@model_aliases_app.command("clear-global")
def model_aliases_clear_global(
    workspace: WorkspaceOption = Path("."),
    reload_config: Annotated[
        bool,
        typer.Option("--reload/--no-reload", help="Request a config reload after writing TOML."),
    ] = True,
) -> None:
    paths = _ensure_paths(workspace)
    clear_model_assignment_default(paths.runtime_root / "millrace.toml")
    _echo_mutated_or_reload(paths.root, reload_config=reload_config)


@model_aliases_app.command("clear-loop")
def model_aliases_clear_loop(
    loop_id: Annotated[str, typer.Argument(help="Graph loop id.")],
    workspace: WorkspaceOption = Path("."),
    reload_config: Annotated[
        bool,
        typer.Option("--reload/--no-reload", help="Request a config reload after writing TOML."),
    ] = True,
) -> None:
    paths = _ensure_paths(workspace)
    clear_model_assignment_loop(paths.runtime_root / "millrace.toml", loop_id=loop_id)
    _echo_mutated_or_reload(paths.root, reload_config=reload_config)


@model_aliases_app.command("clear-stage")
def model_aliases_clear_stage(
    stage: Annotated[str, typer.Argument(help="Stage key or stage kind id.")],
    workspace: WorkspaceOption = Path("."),
    reload_config: Annotated[
        bool,
        typer.Option("--reload/--no-reload", help="Request a config reload after writing TOML."),
    ] = True,
) -> None:
    paths = _ensure_paths(workspace)
    clear_model_assignment_stage(paths.runtime_root / "millrace.toml", stage=stage)
    _echo_mutated_or_reload(paths.root, reload_config=reload_config)


def _echo_mutated_or_reload(workspace: Path, *, reload_config: bool) -> None:
    if not reload_config:
        typer.echo("updated: true")
        return
    result = _cli_api().RuntimeControl(_ensure_paths(workspace)).reload_config()
    _print_control_result(result)


def _validate_alias_id(alias_id: str) -> None:
    if not _SAFE_ALIAS_PATTERN.fullmatch(alias_id):
        raise typer.BadParameter("alias id must match ^[A-Za-z0-9._-]+$")


def _validate_alias_value(field: str, value: str) -> None:
    if not value or not _SAFE_VALUE_PATTERN.fullmatch(value):
        raise typer.BadParameter(f"{field} must match ^[A-Za-z0-9._:/+-]+$")


__all__ = ["model_aliases_app"]
