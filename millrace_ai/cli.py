"""Millrace MVP CLI surface."""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Annotated, Sequence

import typer
from click.exceptions import ClickException
from click.exceptions import Exit as ClickExit
from pydantic import ValidationError

from millrace_ai.compiler import CompileOutcome, compile_and_persist_workspace_plan
from millrace_ai.config import RuntimeConfig, load_runtime_config
from millrace_ai.contracts import CompileDiagnostics, IncidentDocument, ResultClass, SpecDocument, TaskDocument
from millrace_ai.control import ControlActionResult, RuntimeControl
from millrace_ai.doctor import run_workspace_doctor
from millrace_ai.modes import BUILTIN_MODE_PATHS, load_builtin_mode_definition
from millrace_ai.paths import WorkspacePaths, bootstrap_workspace, workspace_paths
from millrace_ai.run_inspection import (
    InspectedRunSummary,
    inspect_run_id,
    list_runs,
    select_primary_run_artifact,
)
from millrace_ai.runners.adapters.codex_cli import CodexCliRunnerAdapter
from millrace_ai.runners.dispatcher import StageRunnerDispatcher
from millrace_ai.runners.registry import RunnerRegistry
from millrace_ai.runtime import RuntimeEngine, RuntimeTickOutcome
from millrace_ai.state_store import load_snapshot
from millrace_ai.work_documents import parse_work_document_as, read_json_import

_SAFE_WORK_ITEM_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

app = typer.Typer(add_completion=False, no_args_is_help=False)
run_app = typer.Typer(add_completion=False, no_args_is_help=True)
queue_app = typer.Typer(add_completion=False, no_args_is_help=True)
control_app = typer.Typer(add_completion=False, no_args_is_help=True)
status_app = typer.Typer(add_completion=False, no_args_is_help=False)
runs_app = typer.Typer(add_completion=False, no_args_is_help=True)
planning_app = typer.Typer(add_completion=False, no_args_is_help=True)
config_app = typer.Typer(add_completion=False, no_args_is_help=True)
modes_app = typer.Typer(add_completion=False, no_args_is_help=True)
compile_app = typer.Typer(add_completion=False, no_args_is_help=True)

app.add_typer(run_app, name="run")
app.add_typer(queue_app, name="queue")
app.add_typer(control_app, name="control")
app.add_typer(status_app, name="status")
app.add_typer(runs_app, name="runs")
app.add_typer(planning_app, name="planning")
app.add_typer(config_app, name="config")
app.add_typer(modes_app, name="modes")
app.add_typer(compile_app, name="compile")

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


def _run_once_exit_code(outcome: RuntimeTickOutcome) -> int:
    failure_class = outcome.stage_result.metadata.get("failure_class")
    if isinstance(failure_class, str) and failure_class in {
        "runner_transport_failure",
        "provider_failure",
    }:
        return 1

    if outcome.stage_result.result_class is ResultClass.RECOVERABLE_FAILURE:
        return 1

    return 0


def _value(value: object) -> str:
    if value is None:
        return "none"
    enum_value = getattr(value, "value", None)
    if isinstance(enum_value, str):
        return enum_value
    return str(value)


def _print_control_result(result: ControlActionResult) -> None:
    typer.echo(f"action: {result.action.value}")
    typer.echo(f"mode: {result.mode}")
    typer.echo(f"applied: {'true' if result.applied else 'false'}")
    typer.echo(f"detail: {result.detail}")
    if result.command_id is not None:
        typer.echo(f"command_id: {result.command_id}")
    if result.mailbox_path is not None:
        typer.echo(f"mailbox_path: {result.mailbox_path}")
    if result.artifact_path is not None:
        typer.echo(f"artifact_path: {result.artifact_path}")


def _render_status_lines(paths: WorkspacePaths) -> tuple[str, ...]:
    snapshot = load_snapshot(paths)

    execution_queue_depth = len(tuple(paths.tasks_queue_dir.glob("*.md")))
    planning_queue_depth = len(tuple(paths.specs_queue_dir.glob("*.md"))) + len(
        tuple(paths.incidents_incoming_dir.glob("*.md"))
    )

    lines = [
        f"workspace: {paths.root}",
        f"runtime_mode: {snapshot.runtime_mode.value}",
        f"process_running: {'true' if snapshot.process_running else 'false'}",
        f"paused: {'true' if snapshot.paused else 'false'}",
        f"stop_requested: {'true' if snapshot.stop_requested else 'false'}",
        f"active_mode_id: {snapshot.active_mode_id}",
        f"compiled_plan_id: {snapshot.compiled_plan_id}",
        f"active_plane: {_value(snapshot.active_plane)}",
        f"active_stage: {_value(snapshot.active_stage)}",
        f"active_work_item_kind: {_value(snapshot.active_work_item_kind)}",
        f"active_work_item_id: {_value(snapshot.active_work_item_id)}",
        f"execution_queue_depth: {execution_queue_depth}",
        f"planning_queue_depth: {planning_queue_depth}",
        f"execution_status_marker: {snapshot.execution_status_marker}",
        f"planning_status_marker: {snapshot.planning_status_marker}",
    ]
    if snapshot.current_failure_class:
        lines.append(f"current_failure_class: {snapshot.current_failure_class}")
        for label, count in (
            ("troubleshoot_attempt_count", snapshot.troubleshoot_attempt_count),
            ("mechanic_attempt_count", snapshot.mechanic_attempt_count),
            ("fix_cycle_count", snapshot.fix_cycle_count),
            ("consultant_invocations", snapshot.consultant_invocations),
        ):
            if count > 0:
                lines.append(f"{label}: {count}")
    return tuple(lines)


def _render_runs_ls_lines(paths: WorkspacePaths) -> tuple[str, ...]:
    lines: list[str] = []
    for index, summary in enumerate(list_runs(paths)):
        if index > 0:
            lines.append("")
        lines.extend(
            (
                f"run_id: {summary.run_id}",
                f"status: {summary.status}",
                f"work_item_kind: {_value(summary.work_item_kind)}",
                f"work_item_id: {_value(summary.work_item_id)}",
                f"failure_class: {_value(summary.failure_class)}",
            )
        )
    return tuple(lines)


def _render_run_show_lines(summary: InspectedRunSummary) -> tuple[str, ...]:
    lines = [
        f"run_id: {summary.run_id}",
        f"status: {summary.status}",
        f"work_item_kind: {_value(summary.work_item_kind)}",
        f"work_item_id: {_value(summary.work_item_id)}",
        f"failure_class: {_value(summary.failure_class)}",
        f"troubleshoot_report_path: {_value(summary.troubleshoot_report_path)}",
        f"primary_stdout_path: {_value(summary.primary_stdout_path)}",
        f"primary_stderr_path: {_value(summary.primary_stderr_path)}",
        f"stage_result_count: {len(summary.stage_results)}",
    ]
    for note in summary.notes:
        lines.append(f"note: {note}")
    for stage_result in summary.stage_results:
        lines.extend(
            (
                f"stage_result_path: {stage_result.stage_result_path}",
                f"stage: {stage_result.stage}",
                f"terminal_result: {stage_result.terminal_result}",
                f"result_class: {stage_result.result_class}",
                f"runner_name: {_value(stage_result.runner_name)}",
                f"model_name: {_value(stage_result.model_name)}",
                f"stdout_path: {_value(stage_result.stdout_path)}",
                f"stderr_path: {_value(stage_result.stderr_path)}",
                f"report_artifact: {_value(stage_result.report_artifact)}",
            )
        )
        for artifact_path in stage_result.artifact_paths:
            lines.append(f"artifact_path: {artifact_path}")
    return tuple(lines)


def _resolve_run_artifact_path(run_dir: str, candidate: str) -> Path:
    path = Path(candidate)
    if path.is_absolute():
        return path
    return Path(run_dir) / path


def _render_config_show_lines(paths: WorkspacePaths, config: RuntimeConfig) -> tuple[str, ...]:
    snapshot = load_snapshot(paths)
    return (
        f"default_mode: {config.runtime.default_mode}",
        f"run_style: {config.runtime.run_style.value}",
        f"idle_sleep_seconds: {config.runtime.idle_sleep_seconds}",
        f"watchers.enabled: {'true' if config.watchers.enabled else 'false'}",
        f"config_version: {snapshot.config_version}",
        f"last_reload_outcome: {_value(snapshot.last_reload_outcome)}",
        f"last_reload_error: {_value(snapshot.last_reload_error)}",
    )


def _print_status(paths: WorkspacePaths) -> None:
    for line in _render_status_lines(paths):
        typer.echo(line)


def _print_statuses(paths_list: Sequence[WorkspacePaths]) -> None:
    for index, paths in enumerate(paths_list):
        if index > 0:
            typer.echo("")
        _print_status(paths)


def _render_compile_diagnostics(outcome: CompileOutcome) -> int:
    diagnostics: CompileDiagnostics = outcome.diagnostics
    typer.echo(f"ok: {'true' if diagnostics.ok else 'false'}")
    typer.echo(f"mode_id: {diagnostics.mode_id}")
    typer.echo(f"used_last_known_good: {'true' if outcome.used_last_known_good else 'false'}")
    for warning in diagnostics.warnings:
        typer.echo(f"warning: {warning}")
    for error in diagnostics.errors:
        typer.echo(f"error: {error}")
    return 0 if diagnostics.ok else 1


@run_app.command("once")
def run_once(
    workspace: WorkspaceOption = Path("."),
    mode: Annotated[str | None, typer.Option("--mode", help="Override mode id.")] = None,
    config_path: ConfigOption = None,
) -> None:
    paths = _ensure_paths(workspace)
    resolved_config_path = _resolve_config_path(paths, config_path)
    try:
        runtime_config = load_runtime_config(resolved_config_path)
        stage_runner = _build_stage_runner(config=runtime_config, workspace_root=paths.root)
    except ValueError as exc:
        raise typer.Exit(code=_print_error(str(exc))) from exc
    engine = RuntimeEngine(
        paths,
        stage_runner=stage_runner,
        config_path=resolved_config_path,
        mode_id=mode,
    )
    try:
        snapshot = engine.startup()
        outcome = engine.tick()
    except Exception as exc:
        raise typer.Exit(code=_print_error(str(exc))) from exc
    typer.echo("run_mode: once")
    typer.echo(f"active_mode_id: {snapshot.active_mode_id}")
    typer.echo(f"compiled_plan_id: {snapshot.compiled_plan_id}")
    typer.echo(f"tick_reason: {_value(outcome.router_decision.reason)}")
    raise typer.Exit(code=_run_once_exit_code(outcome))


@run_app.command("daemon")
def run_daemon(
    workspace: WorkspaceOption = Path("."),
    mode: Annotated[str | None, typer.Option("--mode", help="Override mode id.")] = None,
    config_path: ConfigOption = None,
    max_ticks: Annotated[
        int | None,
        typer.Option("--max-ticks", min=1, help="Stop after this many ticks."),
    ] = None,
) -> None:
    paths = _ensure_paths(workspace)
    resolved_config_path = _resolve_config_path(paths, config_path)
    try:
        runtime_config = load_runtime_config(resolved_config_path)
        stage_runner = _build_stage_runner(config=runtime_config, workspace_root=paths.root)
    except ValueError as exc:
        raise typer.Exit(code=_print_error(str(exc))) from exc
    engine = RuntimeEngine(
        paths,
        stage_runner=stage_runner,
        config_path=resolved_config_path,
        mode_id=mode,
    )
    try:
        snapshot = engine.startup()
    except Exception as exc:
        raise typer.Exit(code=_print_error(str(exc))) from exc

    ticks = 0
    try:
        while True:
            if max_ticks is not None and ticks >= max_ticks:
                break
            try:
                engine.tick()
            except Exception as exc:
                raise typer.Exit(code=_print_error(str(exc))) from exc
            ticks += 1
            runtime_snapshot = engine.snapshot
            if runtime_snapshot is not None and (
                runtime_snapshot.stop_requested or not runtime_snapshot.process_running
            ):
                break
            if max_ticks is None:
                time.sleep(runtime_config.runtime.idle_sleep_seconds)
    except KeyboardInterrupt:
        typer.echo("interrupted: true")

    typer.echo("run_mode: daemon")
    typer.echo(f"active_mode_id: {snapshot.active_mode_id}")
    typer.echo(f"compiled_plan_id: {snapshot.compiled_plan_id}")
    typer.echo(f"ticks: {ticks}")


@status_app.callback(invoke_without_command=True)
def status(
    ctx: typer.Context,
    workspace: WorkspaceOption = Path("."),
) -> None:
    if ctx.invoked_subcommand is None:
        _print_status(_ensure_paths(workspace))


@status_app.command("show")
def status_show(workspace: WorkspaceOption = Path(".")) -> None:
    _print_status(_ensure_paths(workspace))


@status_app.command("watch")
def status_watch(
    workspace: Annotated[
        list[Path] | None,
        typer.Option(
            "--workspace",
            help="Workspace root directory.",
            file_okay=False,
            dir_okay=True,
            writable=True,
            resolve_path=True,
        ),
    ] = None,
    max_updates: Annotated[
        int | None,
        typer.Option("--max-updates", min=1, help="Stop after this many status updates."),
    ] = None,
    interval_seconds: Annotated[
        float,
        typer.Option("--interval-seconds", min=0.0, help="Polling interval between status updates."),
    ] = 1.0,
) -> None:
    workspace_values = workspace if workspace else [Path(".")]
    unique_paths: list[WorkspacePaths] = []
    seen_roots: set[Path] = set()
    for path in workspace_values:
        resolved = _ensure_paths(path)
        if resolved.root in seen_roots:
            continue
        seen_roots.add(resolved.root)
        unique_paths.append(resolved)

    paths_list = tuple(unique_paths)
    update_count = 0

    try:
        while True:
            _print_statuses(paths_list)
            update_count += 1
            if max_updates is not None and update_count >= max_updates:
                break
            typer.echo("")
            time.sleep(interval_seconds)
    except KeyboardInterrupt:
        typer.echo("interrupted: true")


@runs_app.command("ls")
def runs_ls(workspace: WorkspaceOption = Path(".")) -> None:
    for line in _render_runs_ls_lines(_ensure_paths(workspace)):
        typer.echo(line)


@runs_app.command("show")
def runs_show(
    run_id: Annotated[str, typer.Argument(help="Run ID to inspect.")],
    workspace: WorkspaceOption = Path("."),
) -> None:
    summary = inspect_run_id(_ensure_paths(workspace), run_id)
    if summary is None:
        raise typer.Exit(code=_print_error(f"run not found: {run_id}"))
    for line in _render_run_show_lines(summary):
        typer.echo(line)


@runs_app.command("tail")
def runs_tail(
    run_id: Annotated[str, typer.Argument(help="Run ID to tail.")],
    workspace: WorkspaceOption = Path("."),
) -> None:
    summary = inspect_run_id(_ensure_paths(workspace), run_id)
    if summary is None:
        raise typer.Exit(code=_print_error(f"run not found: {run_id}"))
    selected_artifact = select_primary_run_artifact(summary)
    if selected_artifact is None:
        raise typer.Exit(code=_print_error(f"no tailable artifact found for run: {run_id}"))

    artifact_path = _resolve_run_artifact_path(summary.run_dir, selected_artifact)
    try:
        typer.echo(artifact_path.read_text(encoding="utf-8"), nl=False)
    except OSError as exc:
        raise typer.Exit(code=_print_error(str(exc))) from exc


@queue_app.command("ls")
def queue_ls(workspace: WorkspaceOption = Path(".")) -> None:
    paths = _ensure_paths(workspace)
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
    paths = _ensure_paths(workspace)
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
    paths = _ensure_paths(workspace)
    try:
        document = _load_task_document(task_path)
        result = RuntimeControl(paths).add_task(document)
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
    paths = _ensure_paths(workspace)
    try:
        document = _load_spec_document(spec_path)
        result = RuntimeControl(paths).add_spec(document)
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
    paths = _ensure_paths(workspace)
    try:
        markdown = idea_path.read_text(encoding="utf-8")
        result = RuntimeControl(paths).add_idea_markdown(
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


@app.command("add-task")
def add_task(
    task_path: Annotated[Path, typer.Argument(exists=True, file_okay=True, dir_okay=False, resolve_path=True)],
    workspace: WorkspaceOption = Path("."),
) -> None:
    queue_add_task(task_path=task_path, workspace=workspace)


@app.command("add-spec")
def add_spec(
    spec_path: Annotated[Path, typer.Argument(exists=True, file_okay=True, dir_okay=False, resolve_path=True)],
    workspace: WorkspaceOption = Path("."),
) -> None:
    queue_add_spec(spec_path=spec_path, workspace=workspace)


@control_app.command("pause")
def control_pause(workspace: WorkspaceOption = Path(".")) -> None:
    result = RuntimeControl(_ensure_paths(workspace)).pause_runtime()
    _print_control_result(result)


@control_app.command("resume")
def control_resume(workspace: WorkspaceOption = Path(".")) -> None:
    result = RuntimeControl(_ensure_paths(workspace)).resume_runtime()
    _print_control_result(result)


@control_app.command("stop")
def control_stop(workspace: WorkspaceOption = Path(".")) -> None:
    result = RuntimeControl(_ensure_paths(workspace)).stop_runtime()
    _print_control_result(result)


@control_app.command("retry-active")
def control_retry_active(
    workspace: WorkspaceOption = Path("."),
    reason: Annotated[str, typer.Option("--reason", help="Reason attached to retry action.")] = "operator requested retry",
) -> None:
    result = RuntimeControl(_ensure_paths(workspace)).retry_active(reason=reason)
    _print_control_result(result)


@planning_app.command("retry-active")
def planning_retry_active(
    workspace: WorkspaceOption = Path("."),
    reason: Annotated[
        str,
        typer.Option("--reason", help="Reason attached to planning retry action."),
    ] = "operator requested planning retry",
) -> None:
    result = RuntimeControl(_ensure_paths(workspace)).retry_active_planning(reason=reason)
    _print_control_result(result)


@config_app.command("show")
def config_show(
    workspace: WorkspaceOption = Path("."),
    config_path: ConfigOption = None,
) -> None:
    paths = _ensure_paths(workspace)
    resolved_config_path = _resolve_config_path(paths, config_path)
    try:
        config = load_runtime_config(resolved_config_path)
    except (OSError, ValidationError, ValueError) as exc:
        raise typer.Exit(code=_print_error(str(exc))) from exc
    for line in _render_config_show_lines(paths, config):
        typer.echo(line)


@config_app.command("validate")
def config_validate(
    workspace: WorkspaceOption = Path("."),
    config_path: ConfigOption = None,
    mode: Annotated[str | None, typer.Option("--mode", help="Optional mode id override.")] = None,
) -> None:
    paths = _ensure_paths(workspace)
    resolved_config_path = _resolve_config_path(paths, config_path)
    try:
        config = load_runtime_config(resolved_config_path)
        outcome = compile_and_persist_workspace_plan(
            paths,
            config=config,
            requested_mode_id=mode,
        )
    except (OSError, ValidationError, ValueError) as exc:
        raise typer.Exit(code=_print_error(str(exc))) from exc
    raise typer.Exit(code=_render_compile_diagnostics(outcome))


@config_app.command("reload")
def config_reload(workspace: WorkspaceOption = Path(".")) -> None:
    result = RuntimeControl(_ensure_paths(workspace)).reload_config()
    _print_control_result(result)


@control_app.command("clear-stale-state")
def control_clear_stale_state(
    workspace: WorkspaceOption = Path("."),
    reason: Annotated[
        str,
        typer.Option("--reason", help="Reason attached to stale-state clear action."),
    ] = "operator requested stale-state clear",
) -> None:
    result = RuntimeControl(_ensure_paths(workspace)).clear_stale_state(reason=reason)
    _print_control_result(result)


@control_app.command("reload-config")
def control_reload_config(workspace: WorkspaceOption = Path(".")) -> None:
    result = RuntimeControl(_ensure_paths(workspace)).reload_config()
    _print_control_result(result)


@app.command("pause")
def pause(workspace: WorkspaceOption = Path(".")) -> None:
    control_pause(workspace=workspace)


@app.command("resume")
def resume(workspace: WorkspaceOption = Path(".")) -> None:
    control_resume(workspace=workspace)


@app.command("stop")
def stop(workspace: WorkspaceOption = Path(".")) -> None:
    control_stop(workspace=workspace)


@app.command("retry-active")
def retry_active(
    workspace: WorkspaceOption = Path("."),
    reason: Annotated[str, typer.Option("--reason", help="Reason attached to retry action.")] = "operator requested retry",
) -> None:
    control_retry_active(workspace=workspace, reason=reason)


@app.command("clear-stale-state")
def clear_stale_state(
    workspace: WorkspaceOption = Path("."),
    reason: Annotated[
        str,
        typer.Option("--reason", help="Reason attached to stale-state clear action."),
    ] = "operator requested stale-state clear",
) -> None:
    control_clear_stale_state(workspace=workspace, reason=reason)


@app.command("reload-config")
def reload_config(workspace: WorkspaceOption = Path(".")) -> None:
    control_reload_config(workspace=workspace)


@app.command("doctor")
def doctor(workspace: WorkspaceOption = Path(".")) -> None:
    report = run_workspace_doctor(_ensure_paths(workspace))
    typer.echo(f"ok: {'true' if report.ok else 'false'}")
    typer.echo(f"errors: {len(report.errors)}")
    typer.echo(f"warnings: {len(report.warnings)}")
    for issue in report.errors:
        location = str(issue.path) if issue.path is not None else "none"
        typer.echo(f"error: {issue.code} {location} {issue.message}")
    for warning in report.warnings:
        location = str(warning.path) if warning.path is not None else "none"
        typer.echo(f"warning: {warning.code} {location} {warning.message}")
    raise typer.Exit(code=0 if report.ok else 1)


@modes_app.command("list")
def list_modes() -> None:
    for mode_id in sorted(BUILTIN_MODE_PATHS):
        mode_definition = load_builtin_mode_definition(mode_id)
        typer.echo(
            f"{mode_definition.mode_id}: execution_loop={mode_definition.execution_loop_id} "
            f"planning_loop={mode_definition.planning_loop_id}"
        )


@modes_app.command("show")
def show_mode(mode_id: Annotated[str, typer.Argument(help="Mode ID to inspect.")]) -> None:
    mode_definition = load_builtin_mode_definition(mode_id)
    typer.echo(f"mode_id: {mode_definition.mode_id}")
    typer.echo(f"execution_loop_id: {mode_definition.execution_loop_id}")
    typer.echo(f"planning_loop_id: {mode_definition.planning_loop_id}")


@compile_app.command("validate")
def compile_validate(
    workspace: WorkspaceOption = Path("."),
    mode: Annotated[str | None, typer.Option("--mode", help="Mode id to compile.")] = None,
    config_path: ConfigOption = None,
) -> None:
    paths = _ensure_paths(workspace)
    config: RuntimeConfig = load_runtime_config(_resolve_config_path(paths, config_path))
    outcome = compile_and_persist_workspace_plan(
        paths,
        config=config,
        requested_mode_id=mode,
    )
    raise typer.Exit(code=_render_compile_diagnostics(outcome))


@compile_app.command("show")
def compile_show(
    workspace: WorkspaceOption = Path("."),
    mode: Annotated[str | None, typer.Option("--mode", help="Mode id to compile.")] = None,
    config_path: ConfigOption = None,
) -> None:
    paths = _ensure_paths(workspace)
    config: RuntimeConfig = load_runtime_config(_resolve_config_path(paths, config_path))
    outcome = compile_and_persist_workspace_plan(
        paths,
        config=config,
        requested_mode_id=mode,
    )
    exit_code = _render_compile_diagnostics(outcome)

    if outcome.active_plan is not None:
        plan = outcome.active_plan
        typer.echo(f"compiled_plan_id: {plan.compiled_plan_id}")
        typer.echo(f"execution_loop_id: {plan.execution_loop_id}")
        typer.echo(f"planning_loop_id: {plan.planning_loop_id}")
        for stage_plan in sorted(plan.stage_plans, key=lambda item: (item.plane.value, item.stage.value)):
            typer.echo(f"stage: {stage_plan.plane.value}.{stage_plan.stage.value}")
            typer.echo(f"entrypoint_path: {stage_plan.entrypoint_path}")
            typer.echo(f"required_skills: {', '.join(stage_plan.required_skills) if stage_plan.required_skills else 'none'}")
            typer.echo(
                "attached_skills: "
                f"{', '.join(stage_plan.attached_skill_additions) if stage_plan.attached_skill_additions else 'none'}"
            )

    raise typer.Exit(code=exit_code)


def _print_error(message: str) -> int:
    typer.echo(f"error: {message}")
    return 1


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


__all__ = ["app", "main"]
