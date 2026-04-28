"""Runtime execution command group."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Annotated, Any

import typer

from millrace_ai.cli.errors import _print_error
from millrace_ai.cli.formatting import _run_once_exit_code, _value
from millrace_ai.cli.shared import ConfigOption, WorkspaceOption, _cli_api, _require_paths, _resolve_config_path
from millrace_ai.runtime.engine import RuntimeEngine as RealRuntimeEngine

run_app = typer.Typer(add_completion=False, no_args_is_help=True)


@run_app.command("once")
def run_once(
    workspace: WorkspaceOption = Path("."),
    mode: Annotated[str | None, typer.Option("--mode", help="Override mode id.")] = None,
    config_path: ConfigOption = None,
) -> None:
    cli_api = _cli_api()
    paths = _require_paths(workspace)
    resolved_config_path = _resolve_config_path(paths, config_path)
    try:
        runtime_config = cli_api.load_runtime_config(resolved_config_path)
        stage_runner = cli_api._build_stage_runner(config=runtime_config, workspace_root=paths.root)
    except ValueError as exc:
        raise typer.Exit(code=_print_error(str(exc))) from exc
    engine = cli_api.RuntimeEngine(
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
    finally:
        engine.close()
    typer.echo("run_mode: once")
    typer.echo(f"active_mode_id: {snapshot.active_mode_id}")
    typer.echo(f"mode_override: {mode or 'none'}")
    typer.echo(f"compiled_plan_id: {snapshot.compiled_plan_id}")
    typer.echo(f"tick_reason: {_value(outcome.router_decision.reason)}")
    raise typer.Exit(code=_run_once_exit_code(outcome))


@run_app.command("daemon")
def run_daemon(
    workspace: WorkspaceOption = Path("."),
    mode: Annotated[str | None, typer.Option("--mode", help="Override mode id.")] = None,
    config_path: ConfigOption = None,
    monitor_mode: Annotated[
        str,
        typer.Option(
            "--monitor",
            help="Optional terminal monitor mode: none or basic.",
            case_sensitive=False,
        ),
    ] = "none",
    max_ticks: Annotated[
        int | None,
        typer.Option("--max-ticks", min=1, help="Stop after this many ticks."),
    ] = None,
    monitor_log: Annotated[
        Path | None,
        typer.Option(
            "--monitor-log",
            help="Write basic monitor output to this file without changing stdout monitor mode.",
            dir_okay=False,
            resolve_path=True,
        ),
    ] = None,
) -> None:
    cli_api = _cli_api()
    paths = _require_paths(workspace)
    resolved_config_path = _resolve_config_path(paths, config_path)
    try:
        runtime_config = cli_api.load_runtime_config(resolved_config_path)
        stage_runner = cli_api._build_stage_runner(config=runtime_config, workspace_root=paths.root)
    except ValueError as exc:
        raise typer.Exit(code=_print_error(str(exc))) from exc
    normalized_monitor_mode = monitor_mode.lower()
    if normalized_monitor_mode not in {"basic", "none"}:
        raise typer.Exit(code=_print_error(f"unknown monitor mode: {monitor_mode}"))

    monitor_log_handle = None
    monitor_sinks = []
    if normalized_monitor_mode == "basic":
        monitor_sinks.append(cli_api.BasicTerminalMonitor(stream=sys.stdout))
    if monitor_log is not None:
        monitor_log.parent.mkdir(parents=True, exist_ok=True)
        monitor_log_handle = monitor_log.open("a", encoding="utf-8", buffering=1)
        monitor_sinks.append(cli_api.BasicTerminalMonitor(stream=monitor_log_handle))
    if not monitor_sinks:
        monitor = cli_api.NullRuntimeMonitorSink()
    elif len(monitor_sinks) == 1:
        monitor = monitor_sinks[0]
    else:
        monitor = _FanoutRuntimeMonitorSink(tuple(monitor_sinks))

    engine = cli_api.RuntimeEngine(
        paths,
        stage_runner=stage_runner,
        config_path=resolved_config_path,
        mode_id=mode,
        monitor=monitor,
    )
    try:
        try:
            snapshot = engine.startup()
        except Exception as exc:
            raise typer.Exit(code=_print_error(str(exc))) from exc

        try:
            if _uses_daemon_supervisor(engine, cli_api):
                try:
                    ticks = asyncio.run(
                        _run_daemon_supervisor_loop(
                            engine,
                            supervisor_cls=cli_api.RuntimeDaemonSupervisor,
                            idle_sleep_seconds=runtime_config.runtime.idle_sleep_seconds,
                            max_ticks=max_ticks,
                        )
                    )
                except Exception as exc:
                    raise typer.Exit(code=_print_error(str(exc))) from exc
            else:
                ticks = _run_daemon_tick_loop(
                    engine,
                    cli_api=cli_api,
                    idle_sleep_seconds=runtime_config.runtime.idle_sleep_seconds,
                    max_ticks=max_ticks,
                )
        except KeyboardInterrupt:
            typer.echo("interrupted: true")
    finally:
        close = getattr(engine, "close", None)
        if callable(close):
            try:
                close()
            finally:
                if monitor_log_handle is not None:
                    monitor_log_handle.close()
        elif monitor_log_handle is not None:
            monitor_log_handle.close()

    typer.echo("run_mode: daemon")
    typer.echo(f"active_mode_id: {snapshot.active_mode_id}")
    typer.echo(f"mode_override: {mode or 'none'}")
    typer.echo(f"compiled_plan_id: {snapshot.compiled_plan_id}")
    typer.echo(f"ticks: {ticks}")


class _FanoutRuntimeMonitorSink:
    def __init__(self, sinks: tuple[Any, ...]) -> None:
        self._sinks = sinks

    def emit(self, event: object) -> None:
        for sink in self._sinks:
            sink.emit(event)


def _uses_daemon_supervisor(engine: object, cli_api: Any) -> bool:
    return isinstance(engine, RealRuntimeEngine) and hasattr(cli_api, "RuntimeDaemonSupervisor")


async def _run_daemon_supervisor_loop(
    engine: Any,
    *,
    supervisor_cls: Any,
    idle_sleep_seconds: float,
    max_ticks: int | None,
) -> int:
    supervisor = supervisor_cls(engine)
    ticks = 0
    while True:
        if max_ticks is not None and ticks >= max_ticks:
            break
        await supervisor.run_cycle()
        await supervisor.drain_completed(wait=False)
        ticks += 1
        runtime_snapshot = engine.snapshot
        if runtime_snapshot is not None and (
            runtime_snapshot.stop_requested or not runtime_snapshot.process_running
        ):
            if not supervisor.active_worker_planes:
                break
        if max_ticks is None:
            await supervisor.wait_for_next_completion_or_timeout(idle_sleep_seconds)
    if max_ticks is not None:
        await supervisor.drain_completed(wait=True)
    return ticks


def _run_daemon_tick_loop(
    engine: Any,
    *,
    cli_api: Any,
    idle_sleep_seconds: float,
    max_ticks: int | None,
) -> int:
    ticks = 0
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
            cli_api.time.sleep(idle_sleep_seconds)
    return ticks
