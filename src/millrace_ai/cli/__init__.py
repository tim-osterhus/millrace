"""Stable CLI package surface and monkeypatchable dependency exports."""

from __future__ import annotations

import time
from typing import Sequence

import typer
from click.exceptions import ClickException
from click.exceptions import Exit as ClickExit

from millrace_ai.compiler import CompileOutcome, compile_and_persist_workspace_plan
from millrace_ai.config import RuntimeConfig, load_runtime_config
from millrace_ai.control import ControlActionResult, RuntimeControl
from millrace_ai.doctor import run_workspace_doctor
from millrace_ai.modes import BUILTIN_MODE_PATHS, load_builtin_mode_definition
from millrace_ai.run_inspection import (
    InspectedRunSummary,
    inspect_run_id,
    list_runs,
    select_primary_run_artifact,
)
from millrace_ai.runtime import RuntimeEngine, RuntimeTickOutcome

from .app import app
from .shared import _build_stage_runner


def main(argv: Sequence[str] | None = None) -> int:
    args = argv if argv is not None else None
    try:
        outcome = app(args=args, standalone_mode=False)
    except typer.Exit as exc:
        return int(exc.exit_code)
    except ClickExit as exc:
        return int(exc.exit_code)
    except ClickException as exc:
        typer.echo(f"error: {exc}")
        return 1
    if isinstance(outcome, int):
        return outcome
    return 0


__all__ = [
    "BUILTIN_MODE_PATHS",
    "CompileOutcome",
    "ControlActionResult",
    "InspectedRunSummary",
    "RuntimeConfig",
    "RuntimeControl",
    "RuntimeEngine",
    "RuntimeTickOutcome",
    "_build_stage_runner",
    "app",
    "compile_and_persist_workspace_plan",
    "inspect_run_id",
    "list_runs",
    "load_builtin_mode_definition",
    "load_runtime_config",
    "main",
    "run_workspace_doctor",
    "select_primary_run_artifact",
    "time",
]
