"""Typer-based runtime control CLI."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any
import json

import typer

from .cli_rendering import (
    _asset_inventory_lines,
    _legacy_policy_lines,
    _legacy_unmapped_lines,
    _selection_explanation_lines,
    _selection_lines,
    render_doctor,
    render_follow_event,
    render_health,
    render_log_events,
    render_operation,
    render_publish_commit,
    render_publish_preflight,
    render_queue,
    render_research_report,
    render_run_provenance,
    render_staging_sync,
    render_status,
)
from .control import ConfigShowReport, ControlError, EngineControl
from .events import EventRecord


app = typer.Typer(add_completion=False, help="Control the Millrace runtime.")
config_app = typer.Typer(help="Inspect or mutate runtime config.")
queue_app = typer.Typer(help="Inspect visible execution queues.")
research_app = typer.Typer(help="Inspect research runtime state and history.")
publish_app = typer.Typer(help="Sync and publish the staging surface.")
app.add_typer(config_app, name="config")
app.add_typer(queue_app, name="queue")
app.add_typer(research_app, name="research")
app.add_typer(publish_app, name="publish")


@dataclass(frozen=True, slots=True)
class CLIContext:
    config_path: Path


def _json_output(payload: Any, *, err: bool = False) -> None:
    typer.echo(json.dumps(payload, indent=2, sort_keys=True), err=err)


def _cli_context(ctx: typer.Context) -> CLIContext:
    cli_context = ctx.obj
    if not isinstance(cli_context, CLIContext):
        raise typer.Exit(code=1)
    return cli_context


def _control(ctx: typer.Context) -> EngineControl:
    return EngineControl(_cli_context(ctx).config_path)


def _config_path(ctx: typer.Context) -> Path:
    return _cli_context(ctx).config_path


def _exit_control_error(error: ControlError, *, json_mode: bool) -> None:
    if json_mode:
        _json_output({"error": str(error)}, err=True)
    else:
        typer.echo(str(error), err=True)
    raise typer.Exit(code=1)


def _run_expected(action: Callable[[], Any], *, json_mode: bool) -> Any:
    try:
        return action()
    except ControlError as exc:
        _exit_control_error(exc, json_mode=json_mode)


def _iter_expected(events: Any, *, json_mode: bool) -> Any:
    try:
        yield from events
    except ControlError as exc:
        _exit_control_error(exc, json_mode=json_mode)


@app.callback()
def root(
    ctx: typer.Context,
    config_path: Annotated[
        Path,
        typer.Option(
            "--config",
            file_okay=True,
            dir_okay=False,
            resolve_path=True,
            help="Path to millrace.toml.",
        ),
    ] = Path("millrace.toml"),
) -> None:
    """Prepare CLI-local config context."""

    ctx.obj = CLIContext(config_path=config_path)


@app.command("init")
def init_command(
    destination: Annotated[
        Path,
        typer.Argument(
            file_okay=False,
            dir_okay=True,
            resolve_path=True,
            help="Destination workspace directory.",
        ),
    ],
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            help="Allow a non-empty destination and overwrite manifest-tracked files.",
        ),
    ] = False,
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Initialize a workspace from the packaged baseline bundle."""

    try:
        result = EngineControl.init_workspace(destination, force=force)
    except ControlError as exc:
        raise typer.BadParameter(str(exc), param_hint="destination") from exc
    render_operation(result, json_mode=json_mode)


@app.command("health")
def health_command(
    ctx: typer.Context,
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Run workspace bootstrap and health checks."""

    report = _run_expected(lambda: EngineControl.health_report(_config_path(ctx)), json_mode=json_mode)
    render_health(report, json_mode=json_mode)
    if report.status.value == "fail":
        raise typer.Exit(code=1)


@app.command("doctor")
def doctor_command(
    ctx: typer.Context,
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Run operator preflight for bootstrap and execution readiness."""

    report = _run_expected(lambda: EngineControl.health_report(_config_path(ctx)), json_mode=json_mode)
    render_doctor(report, json_mode=json_mode)
    if not report.bootstrap_ready or not report.execution_ready:
        raise typer.Exit(code=1)


@app.command("start")
def start_command(
    ctx: typer.Context,
    daemon: Annotated[bool, typer.Option("--daemon", help="Run the foreground daemon loop.")] = False,
    once: Annotated[
        bool,
        typer.Option(
            "--once",
            help=(
                "Run one foreground pass. If startup research sync creates new execution backlog from an empty "
                "execution queue, stop after that research pass and run --once again to execute the new task."
            ),
        ),
    ] = False,
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Start the runtime in foreground once or daemon mode."""

    if daemon and once:
        raise typer.BadParameter("use only one of --daemon or --once")
    report = _run_expected(lambda: _control(ctx).start(daemon=daemon, once=once), json_mode=json_mode)
    if json_mode:
        _json_output(report.model_dump(mode="json"))
        return
    typer.echo(
        "\n".join(
            [
                f"Process: {'running' if report.process_running else 'stopped'}",
                f"Paused: {'yes' if report.paused else 'no'}",
                f"Execution status: {report.execution_status.value}",
                f"Research status: {report.research_status.value}",
            ]
        )
    )


@app.command("stop")
def stop_command(ctx: typer.Context, json_mode: Annotated[bool, typer.Option("--json")] = False) -> None:
    """Request daemon stop."""

    render_operation(_run_expected(lambda: _control(ctx).stop(), json_mode=json_mode), json_mode=json_mode)


@app.command("pause")
def pause_command(ctx: typer.Context, json_mode: Annotated[bool, typer.Option("--json")] = False) -> None:
    """Request daemon pause."""

    render_operation(_run_expected(lambda: _control(ctx).pause(), json_mode=json_mode), json_mode=json_mode)


@app.command("resume")
def resume_command(ctx: typer.Context, json_mode: Annotated[bool, typer.Option("--json")] = False) -> None:
    """Request daemon resume."""

    render_operation(_run_expected(lambda: _control(ctx).resume(), json_mode=json_mode), json_mode=json_mode)


@app.command("status")
def status_command(
    ctx: typer.Context,
    detail: Annotated[bool, typer.Option("--detail", help="Include queue detail.")] = False,
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Show runtime status."""

    render_status(
        _run_expected(lambda: _control(ctx).status(detail=detail), json_mode=json_mode),
        json_mode=json_mode,
    )


@app.command("run-provenance")
def run_provenance_command(
    ctx: typer.Context,
    run_id: Annotated[str, typer.Argument(help="Run identifier under agents/runs/.")],
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Show compile-time and runtime provenance for one run."""

    report = _run_expected(lambda: _control(ctx).run_provenance(run_id), json_mode=json_mode)
    render_run_provenance(report, json_mode=json_mode)


@config_app.command("show")
def config_show_command(
    ctx: typer.Context,
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Show the loaded config."""

    report: ConfigShowReport = _run_expected(lambda: _control(ctx).config_show(), json_mode=json_mode)
    if json_mode:
        _json_output(report.model_dump(mode="json"))
        return
    lines = [
        f"Source kind: {report.source.kind}",
        f"Primary path: {report.source.primary_path}",
        f"Config hash: {report.config_hash}",
        f"Execution integration mode: {report.config.execution.integration_mode}",
        f"Quickfix max attempts: {report.config.execution.quickfix_max_attempts}",
        f"Sizing mode: {report.config.sizing.mode}",
        (
            "Repo size thresholds: "
            f"files>={report.config.sizing.repo.file_count_threshold} "
            f"nonempty_lines>={report.config.sizing.repo.nonempty_line_count_threshold}"
        ),
        (
            "Task size thresholds: "
            f"files_to_touch>={report.config.sizing.task.file_count_threshold} "
            f"nonempty_lines>={report.config.sizing.task.nonempty_line_count_threshold} "
            "promotion=2-of-3(files, loc, complexity)"
        ),
        f"Research mode: {report.config.research.mode.value}",
    ]
    if report.source.secondary_paths:
        lines.append("Secondary source paths:")
        lines.extend(f"- {path}" for path in report.source.secondary_paths)
    if report.source.legacy_policy_compatibility is not None:
        lines.extend(_legacy_policy_lines(report.source.legacy_policy_compatibility))
    lines.extend(_legacy_unmapped_lines(report.source.unmapped_keys))
    lines.extend(_selection_explanation_lines(report.selection_explanation))
    lines.extend(_selection_lines(report.selection))
    lines.extend(_asset_inventory_lines(report.assets))
    typer.echo("\n".join(lines))


@config_app.command("set")
def config_set_command(
    ctx: typer.Context,
    key: Annotated[str, typer.Argument(help="Dotted config key.")],
    value: Annotated[str, typer.Argument(help="New value.")],
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Set one dotted config key."""

    render_operation(
        _run_expected(lambda: _control(ctx).config_set(key, value), json_mode=json_mode),
        json_mode=json_mode,
    )


@config_app.command("reload")
def config_reload_command(
    ctx: typer.Context,
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Reload config from disk."""

    render_operation(_run_expected(lambda: _control(ctx).config_reload(), json_mode=json_mode), json_mode=json_mode)


@queue_app.command("inspect")
def queue_inspect_command(
    ctx: typer.Context,
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Show active and backlog task detail."""

    render_queue(
        _run_expected(lambda: _control(ctx).queue_inspect(), json_mode=json_mode),
        json_mode=json_mode,
        detail=True,
    )


@queue_app.command("reorder")
def queue_reorder_command(
    ctx: typer.Context,
    task_ids: Annotated[list[str], typer.Argument(help="Backlog task IDs in final order.")],
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Rewrite the backlog order exactly as provided."""

    if not task_ids:
        raise typer.BadParameter("provide at least one task id to reorder")
    render_operation(
        _run_expected(lambda: _control(ctx).queue_reorder(task_ids), json_mode=json_mode),
        json_mode=json_mode,
    )


@queue_app.callback(invoke_without_command=True)
def queue_root(
    ctx: typer.Context,
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Show queue summary."""

    if ctx.invoked_subcommand is not None:
        return
    render_queue(
        _run_expected(lambda: _control(ctx).queue(), json_mode=json_mode),
        json_mode=json_mode,
        detail=False,
    )


@research_app.command("history")
def research_history_command(
    ctx: typer.Context,
    limit: Annotated[int, typer.Option("--limit", min=0, help="Number of recent research events to show.")] = 20,
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Show recent research-related events."""

    render_log_events(
        _run_expected(lambda: _control(ctx).research_history(limit=limit), json_mode=json_mode),
        json_mode=json_mode,
    )


@research_app.callback(invoke_without_command=True)
def research_root(
    ctx: typer.Context,
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Show research runtime visibility."""

    if ctx.invoked_subcommand is not None:
        return
    report = _run_expected(lambda: _control(ctx).research_report(), json_mode=json_mode)
    render_research_report(report, json_mode=json_mode)


@publish_app.command("sync")
def publish_sync_command(
    ctx: typer.Context,
    staging_repo_dir: Annotated[
        Path | None,
        typer.Option(
            "--staging-repo-dir",
            file_okay=False,
            dir_okay=True,
            resolve_path=True,
            help="Override the default staging repo directory.",
        ),
    ] = None,
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Sync manifest-selected files into the staging repo."""

    report = _run_expected(
        lambda: _control(ctx).publish_sync(staging_repo_dir=staging_repo_dir),
        json_mode=json_mode,
    )
    render_staging_sync(report, json_mode=json_mode)


@publish_app.command("preflight")
def publish_preflight_command(
    ctx: typer.Context,
    staging_repo_dir: Annotated[
        Path | None,
        typer.Option(
            "--staging-repo-dir",
            file_okay=False,
            dir_okay=True,
            resolve_path=True,
            help="Override the default staging repo directory.",
        ),
    ] = None,
    commit_message: Annotated[
        str | None,
        typer.Option("--message", help="Commit message to evaluate."),
    ] = None,
    push: Annotated[
        bool,
        typer.Option("--push/--no-push", help="Check whether a publish push would run."),
    ] = False,
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Show staging commit/publish readiness without mutating git state."""

    report = _run_expected(
        lambda: _control(ctx).publish_preflight(
            staging_repo_dir=staging_repo_dir,
            commit_message=commit_message,
            push=push,
        ),
        json_mode=json_mode,
    )
    render_publish_preflight(report, json_mode=json_mode)


@publish_app.command("commit")
def publish_commit_command(
    ctx: typer.Context,
    staging_repo_dir: Annotated[
        Path | None,
        typer.Option(
            "--staging-repo-dir",
            file_okay=False,
            dir_okay=True,
            resolve_path=True,
            help="Override the default staging repo directory.",
        ),
    ] = None,
    commit_message: Annotated[
        str | None,
        typer.Option("--message", help="Commit message to use."),
    ] = None,
    push: Annotated[
        bool,
        typer.Option("--push/--no-push", help="Push to origin after commit."),
    ] = False,
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Commit staging changes and optionally push them."""

    report = _run_expected(
        lambda: _control(ctx).publish_commit(
            staging_repo_dir=staging_repo_dir,
            commit_message=commit_message,
            push=push,
        ),
        json_mode=json_mode,
    )
    render_publish_commit(report, json_mode=json_mode)


@app.command("add-task")
def add_task_command(
    ctx: typer.Context,
    title: Annotated[str, typer.Argument(help="Task title.")],
    body: Annotated[str | None, typer.Option("--body", help="Optional markdown body.")] = None,
    spec_id: Annotated[str | None, typer.Option("--spec-id", help="Optional spec identifier.")] = None,
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Add one task card to the backlog."""

    render_operation(
        _run_expected(lambda: _control(ctx).add_task(title, body=body, spec_id=spec_id), json_mode=json_mode),
        json_mode=json_mode,
    )


@app.command("add-idea")
def add_idea_command(
    ctx: typer.Context,
    file: Annotated[Path, typer.Argument(exists=True, file_okay=True, dir_okay=False, resolve_path=True)],
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Queue one idea file into `agents/ideas/raw/`."""

    render_operation(_run_expected(lambda: _control(ctx).add_idea(file), json_mode=json_mode), json_mode=json_mode)


@app.command("logs")
def logs_command(
    ctx: typer.Context,
    tail: Annotated[int, typer.Option("--tail", min=0, help="Number of recent events to show.")] = 50,
    follow: Annotated[bool, typer.Option("--follow", help="Stream new events as they arrive.")] = False,
    limit: Annotated[
        int | None,
        typer.Option("--limit", min=1, help="Maximum number of followed events before exiting."),
    ] = None,
    idle_timeout: Annotated[
        float | None,
        typer.Option("--idle-timeout", min=0.1, help="Stop follow mode after this many idle seconds."),
    ] = None,
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Show recent structured runtime events."""

    control = _run_expected(lambda: _control(ctx), json_mode=json_mode)
    if not follow:
        render_log_events(_run_expected(lambda: control.logs(n=tail), json_mode=json_mode), json_mode=json_mode)
        return

    if tail > 0:
        for event in _run_expected(lambda: control.logs(n=tail), json_mode=json_mode):
            render_follow_event(event, json_mode=json_mode)

    followed = 0
    try:
        events = _run_expected(
            lambda: control.events_subscribe(
                start_at_end=True,
                idle_timeout_seconds=idle_timeout,
            ),
            json_mode=json_mode,
        )
        for event in _iter_expected(events, json_mode=json_mode):
            render_follow_event(event, json_mode=json_mode)
            followed += 1
            if limit is not None and followed >= limit:
                break
    except KeyboardInterrupt as exc:
        raise typer.Exit(code=0) from exc


def main() -> None:
    """Run the Typer app."""

    app()
