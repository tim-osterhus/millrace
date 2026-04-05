"""Startup workspace health gate for the Millrace TUI."""

from __future__ import annotations

from pathlib import Path

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Static
from textual.worker import Worker, WorkerState

from ...health import HealthCheckStatus, WorkspaceHealthReport
from ..formatting import compact_display_path
from ..messages import HealthCheckCompleted, HealthCheckFailed
from ..workers import (
    HEALTH_CHECK_WORKER_NAME,
    HEALTH_WORKER_GROUP,
    gateway_failure_from_exception,
    load_health_report,
)


class HealthGateScreen(Screen[None]):
    """Gate bootstrap on the deterministic workspace health report."""

    BINDINGS = [
        ("r", "retry", "Retry"),
        ("f", "open_config", "Open Config"),
        ("l", "open_logs", "Open Logs"),
        ("q", "app.quit", "Quit"),
    ]

    def __init__(self, *, config_path: Path, workspace_path: Path) -> None:
        super().__init__()
        self.config_path = config_path
        self.workspace_path = workspace_path
        self._last_report: WorkspaceHealthReport | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="health-gate-root"):
            yield Static("Millrace Workspace Health", id="health-gate-title")
            yield Static(id="health-gate-body", classes="panel-card")
            with Horizontal(id="health-gate-actions"):
                yield Button("Retry", id="health-gate-retry")
                yield Button("Open Config", id="health-gate-open-config")
                yield Button("Open Logs", id="health-gate-open-logs")
                yield Button("Quit", id="health-gate-quit")

    def on_mount(self) -> None:
        self._render_pending()
        self.action_retry()

    def action_retry(self) -> None:
        self._render_pending()
        self.run_worker(
            lambda: load_health_report(self.config_path),
            name=HEALTH_CHECK_WORKER_NAME,
            group=HEALTH_WORKER_GROUP,
            exclusive=True,
            exit_on_error=False,
            thread=True,
        )

    def action_open_config(self) -> None:
        self._render_config_recovery()

    def action_open_logs(self) -> None:
        self._render_logs_recovery()

    @on(Button.Pressed, "#health-gate-retry")
    def _handle_retry_button(self, event: Button.Pressed) -> None:
        event.stop()
        self.action_retry()

    @on(Button.Pressed, "#health-gate-open-config")
    def _handle_open_config_button(self, event: Button.Pressed) -> None:
        event.stop()
        self.action_open_config()

    @on(Button.Pressed, "#health-gate-open-logs")
    def _handle_open_logs_button(self, event: Button.Pressed) -> None:
        event.stop()
        self.action_open_logs()

    @on(Button.Pressed, "#health-gate-quit")
    def _handle_quit_button(self, event: Button.Pressed) -> None:
        event.stop()
        self.app.exit()

    @on(Worker.StateChanged)
    def _handle_worker_state_changed(self, message: Worker.StateChanged) -> None:
        if message.worker.name != HEALTH_CHECK_WORKER_NAME:
            return
        if message.state == WorkerState.SUCCESS:
            report = message.worker.result
            if isinstance(report, WorkspaceHealthReport):
                self.post_message(HealthCheckCompleted(report))
            return
        if message.state == WorkerState.ERROR and isinstance(message.worker.error, Exception):
            self.post_message(
                HealthCheckFailed(gateway_failure_from_exception("health.check", message.worker.error))
            )

    def on_health_check_completed(self, message: HealthCheckCompleted) -> None:
        self._last_report = message.report
        if message.report.ok:
            self._render_success(message.report)
            enter_shell = getattr(self.app, "enter_shell", None)
            if callable(enter_shell):
                enter_shell(message.report)
            return
        self._render_report(message.report)

    def on_health_check_failed(self, message: HealthCheckFailed) -> None:
        self._update_body(
            [
                *self._context_lines(workspace_path=self.workspace_path, config_path=self.config_path),
                "",
                "Health check crashed before a report could be produced.",
                f"Operation: {message.failure.operation}",
                f"Failure: {message.failure.message}",
                "",
                "Use Open Config or Open Logs for local recovery context, then press Retry or Quit.",
            ]
        )

    def _render_pending(self) -> None:
        self._update_body(
            [
                *self._context_lines(workspace_path=self.workspace_path, config_path=self.config_path),
                "",
                "Running the deterministic workspace health check before the shell becomes interactive.",
            ]
        )

    def _render_success(self, report: WorkspaceHealthReport) -> None:
        self._update_body(
            [
                *self._context_lines(workspace_path=report.workspace_root, config_path=report.config_path),
                "",
                f"Health passed with {report.summary.passed_checks} checks.",
                "Entering the shell.",
            ]
        )

    def _render_report(self, report: WorkspaceHealthReport) -> None:
        status_label = "passed" if report.status == HealthCheckStatus.PASS else "failed"
        lines = [
            *self._context_lines(workspace_path=report.workspace_root, config_path=report.config_path),
            "",
            f"Workspace health {status_label}.",
            (
                f"Checks: {report.summary.passed_checks} passed, "
                f"{report.summary.warning_checks} warnings, {report.summary.failed_checks} failed."
            ),
            "",
        ]
        for check in report.checks:
            if check.status == HealthCheckStatus.PASS:
                continue
            lines.append(f"{check.status.value.upper()}: {check.check_id}: {check.message}")
            for detail in check.details:
                lines.append(f"  - {detail}")
        lines.extend(
            [
                "",
                "Use Open Config or Open Logs for recovery context, then press Retry or Quit.",
            ]
        )
        self._update_body(lines)

    def _render_config_recovery(self) -> None:
        lines = [
            *self._context_lines(workspace_path=self.workspace_path, config_path=self.config_path),
            "",
            "Config recovery surface.",
            "Inspect the resolved config file, then press Retry after correcting the workspace.",
            "",
        ]
        lines.extend(
            self._file_preview(
                self.config_path,
                missing_message="Config file does not exist.",
                empty_message="Config file is empty.",
            )
        )
        self._update_body(lines)

    def _render_logs_recovery(self) -> None:
        log_path = self.workspace_path / "agents" / "engine_events.log"
        lines = [
            *self._context_lines(workspace_path=self.workspace_path, config_path=self.config_path),
            "",
            "Runtime log recovery surface.",
            "Inspect the runtime event log, then press Retry after correcting the workspace.",
            "",
        ]
        lines.extend(
            self._file_preview(
                log_path,
                missing_message="Runtime event log does not exist yet.",
                empty_message="Runtime event log is empty.",
                tail_lines=20,
            )
        )
        self._update_body(lines)

    def _file_preview(
        self,
        path: Path,
        *,
        missing_message: str,
        empty_message: str,
        tail_lines: int | None = None,
    ) -> list[str]:
        lines = [f"Path: {compact_display_path(path)}"]
        try:
            if not path.exists():
                return lines + ["", missing_message]
            content = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as error:
            return lines + ["", f"Unable to read file: {error}"]
        if not content or not any(line.strip() for line in content):
            return lines + ["", empty_message]
        preview = content[-tail_lines:] if tail_lines is not None else content
        lines.extend(["", "Preview:", *preview])
        return lines

    def _context_lines(self, *, workspace_path: Path, config_path: Path) -> list[str]:
        return [
            f"Workspace: {compact_display_path(workspace_path)}",
            f"Config: {compact_display_path(config_path)}",
        ]

    def _update_body(self, lines: list[str]) -> None:
        self.query_one("#health-gate-body", Static).update("\n".join(lines))


__all__ = ["HealthGateScreen"]
