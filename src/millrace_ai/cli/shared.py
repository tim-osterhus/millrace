"""Shared CLI option parsing and workspace-resolution helpers."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated

import typer

from millrace_ai.config import RuntimeConfig
from millrace_ai.contracts import SpecDocument, TaskDocument
from millrace_ai.paths import WorkspacePaths, bootstrap_workspace, workspace_paths
from millrace_ai.runners.adapters.codex_cli import CodexCliRunnerAdapter
from millrace_ai.runners.dispatcher import StageRunnerDispatcher
from millrace_ai.runners.registry import RunnerRegistry
from millrace_ai.work_documents import parse_work_document_as, read_json_import

_SAFE_WORK_ITEM_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

WorkspaceOption = Annotated[
    Path,
    typer.Option(
        "--workspace",
        help="Workspace root directory.",
        file_okay=False,
        dir_okay=True,
        writable=True,
        resolve_path=True,
    ),
]

ConfigOption = Annotated[
    Path | None,
    typer.Option(
        "--config",
        help="Optional runtime config path.",
        file_okay=True,
        dir_okay=False,
        resolve_path=True,
    ),
]


def _ensure_paths(workspace: Path) -> WorkspacePaths:
    return bootstrap_workspace(workspace_paths(workspace))


def _resolve_config_path(paths: WorkspacePaths, config_path: Path | None) -> Path:
    return config_path if config_path is not None else paths.runtime_root / "millrace.toml"


def _load_task_document(input_path: Path) -> TaskDocument:
    if input_path.suffix == ".md":
        return parse_work_document_as(
            input_path.read_text(encoding="utf-8"),
            model=TaskDocument,
            path=input_path,
        )
    if input_path.suffix == ".json":
        return read_json_import(input_path, model=TaskDocument)
    raise ValueError("task import path must end with .md or .json")


def _load_spec_document(input_path: Path) -> SpecDocument:
    if input_path.suffix == ".md":
        return parse_work_document_as(
            input_path.read_text(encoding="utf-8"),
            model=SpecDocument,
            path=input_path,
        )
    if input_path.suffix == ".json":
        return read_json_import(input_path, model=SpecDocument)
    raise ValueError("spec import path must end with .md or .json")


def _queue_lookup(
    paths: WorkspacePaths,
    *,
    work_item_id: str,
) -> tuple[str, str, Path] | None:
    directories: tuple[tuple[str, str, Path], ...] = (
        ("task", "queue", paths.tasks_queue_dir),
        ("task", "active", paths.tasks_active_dir),
        ("task", "done", paths.tasks_done_dir),
        ("task", "blocked", paths.tasks_blocked_dir),
        ("spec", "queue", paths.specs_queue_dir),
        ("spec", "active", paths.specs_active_dir),
        ("spec", "done", paths.specs_done_dir),
        ("spec", "blocked", paths.specs_blocked_dir),
        ("incident", "incoming", paths.incidents_incoming_dir),
        ("incident", "active", paths.incidents_active_dir),
        ("incident", "resolved", paths.incidents_resolved_dir),
        ("incident", "blocked", paths.incidents_blocked_dir),
    )
    for kind, state, directory in directories:
        candidate = directory / f"{work_item_id}.md"
        if candidate.is_file():
            return kind, state, candidate
    return None


def _validate_work_item_id(value: str) -> str:
    cleaned = value.strip()
    if cleaned != value:
        raise ValueError("work_item_id must not include surrounding whitespace")
    if not _SAFE_WORK_ITEM_ID_PATTERN.fullmatch(cleaned):
        raise ValueError(f"work_item_id must match {_SAFE_WORK_ITEM_ID_PATTERN.pattern}")
    return cleaned


def _build_stage_runner(*, config: RuntimeConfig, workspace_root: Path) -> StageRunnerDispatcher:
    registry = RunnerRegistry()
    registry.register(CodexCliRunnerAdapter(config=config, workspace_root=workspace_root))
    _validate_configured_stage_runners(config=config, registry=registry)
    return StageRunnerDispatcher(registry=registry, config=config)


def _validate_configured_stage_runners(*, config: RuntimeConfig, registry: RunnerRegistry) -> None:
    unknown = sorted(
        {
            stage_config.runner.strip()
            for stage_config in config.stages.values()
            if stage_config.runner is not None
            and stage_config.runner.strip()
            and registry.get(stage_config.runner.strip()) is None
        }
    )
    if unknown:
        names = ", ".join(unknown)
        raise ValueError(f"Unknown configured stage runner(s): {names}")


def _cli_api():
    import millrace_ai.cli as cli_api

    return cli_api


__all__ = [
    "ConfigOption",
    "WorkspaceOption",
    "_build_stage_runner",
    "_cli_api",
    "_ensure_paths",
    "_load_spec_document",
    "_load_task_document",
    "_queue_lookup",
    "_resolve_config_path",
    "_validate_configured_stage_runners",
    "_validate_work_item_id",
]
