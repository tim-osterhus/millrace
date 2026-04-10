"""Recent runs panel with concise provenance-backed summaries."""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widgets import ContentSwitcher, DataTable, Static

from ..formatting import (
    compact_run_label,
    format_timestamp,
    run_operator_alert,
    run_operator_summary_lines,
    run_summary_lines,
)
from ..models import DisplayMode, GatewayFailure, RunsOverviewView, RunSummaryView
from .progressive_disclosure import append_panel_failure_lines, collapse_operator_text


class RunsPanel(Static):
    """Focusable recent-runs list that opens run detail on selection."""

    can_focus = True
    BINDINGS = (
        Binding("up", "cursor_up", show=False),
        Binding("down", "cursor_down", show=False),
        Binding("home", "cursor_home", show=False),
        Binding("end", "cursor_end", show=False),
        Binding("enter", "submit_selection", show=False),
    )

    class RunRequested(Message):
        """Posted when the operator wants provenance detail for the selected run."""

        bubble = True

        def __init__(self, run_id: str) -> None:
            super().__init__()
            self.run_id = run_id

    class SelectionChanged(Message):
        """Posted when the highlighted run selection changes."""

        bubble = True

        def __init__(self, run_id: str | None) -> None:
            super().__init__()
            self.run_id = run_id

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__("", id=id, classes="panel-card", markup=False)
        self.border_title = "Runs"
        self._runs: RunsOverviewView | None = None
        self._failure: GatewayFailure | None = None
        self._selected_run_id: str | None = None
        self._requested_run_id: str | None = None
        self._display_mode: DisplayMode = DisplayMode.OPERATOR

    def compose(self) -> ComposeResult:
        with ContentSwitcher(initial="runs-operator", id="runs-mode-switcher"):
            with Vertical(id="runs-operator", classes="panel-mode-body"):
                yield self._section_card("runs-status", "Status")
                with Horizontal(classes="panel-metrics"):
                    yield self._metric_card("runs-recent", "Recent")
                    yield self._metric_card("runs-flagged", "Flagged")
                    yield self._metric_card("runs-scanned", "Scanned")
                yield self._section_card("runs-request", "Requested run")
                with Vertical(id="runs-list-card", classes="overview-card panel-section-card panel-list-card"):
                    yield Static("Recent runs", classes="overview-card-label")
                    yield Static("--", id="runs-list-headline", classes="overview-card-headline")
                    yield Static("", id="runs-list-detail", classes="overview-card-detail")
                    yield DataTable(id="runs-table", classes="panel-data-table")
                yield self._section_card("runs-actions", "Actions")
            yield Static("", id="runs-debug", classes="panel-debug-body")

    @staticmethod
    def _metric_card(suffix: str, title: str) -> Vertical:
        return Vertical(
            Static(title, classes="overview-card-label"),
            Static("--", id=f"{suffix}-value", classes="overview-card-value"),
            Static("", id=f"{suffix}-meta", classes="overview-card-meta"),
            classes="overview-card panel-summary-card",
            id=f"{suffix}-card",
        )

    @staticmethod
    def _section_card(suffix: str, title: str) -> Vertical:
        return Vertical(
            Static(title, classes="overview-card-label"),
            Static("--", id=f"{suffix}-headline", classes="overview-card-headline"),
            Static("", id=f"{suffix}-detail", classes="overview-card-detail"),
            classes="overview-card panel-section-card",
            id=f"{suffix}-card",
        )

    @property
    def selected_run_id(self) -> str | None:
        return self._selected_run_id

    def on_mount(self) -> None:
        table = self.query_one("#runs-table", DataTable)
        table.cursor_type = "row"
        table.zebra_stripes = True
        table.add_column("State", key="state", width=7)
        table.add_column("Run", key="run", width=30)
        table.add_column("Selection", key="selection", width=18)
        table.add_column("When", key="when", width=10)
        table.add_column("Status", key="status", width=18)
        self._render_state()

    @on(DataTable.RowHighlighted, "#runs-table")
    def _handle_runs_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        run_id = event.row_key.value
        if run_id.startswith("__runs-empty__") or run_id == self._selected_run_id:
            return
        self._selected_run_id = run_id
        self.post_message(self.SelectionChanged(run_id))

    @on(DataTable.RowSelected, "#runs-table")
    def _handle_runs_row_selected(self, event: DataTable.RowSelected) -> None:
        run_id = event.row_key.value
        if run_id.startswith("__runs-empty__"):
            return
        if run_id != self._selected_run_id:
            self._selected_run_id = run_id
            self.post_message(self.SelectionChanged(run_id))
        self.action_submit_selection()

    def show_snapshot(
        self,
        runs: RunsOverviewView | None,
        *,
        requested_run_id: str | None = None,
        failure: GatewayFailure | None = None,
        display_mode: DisplayMode = DisplayMode.OPERATOR,
    ) -> None:
        self._runs = runs
        self._failure = failure
        self._display_mode = display_mode
        normalized_request = " ".join((requested_run_id or "").split()) or None
        self._requested_run_id = normalized_request
        self._reconcile_selection(preferred_run_id=normalized_request)
        if self.is_mounted:
            self._render_state()

    def summary_text(self) -> str:
        if self._display_mode is DisplayMode.DEBUG:
            return self._render_debug_text()
        return self._render_operator_text()

    def action_cursor_up(self) -> None:
        self._move_selection(-1)

    def action_cursor_down(self) -> None:
        self._move_selection(1)

    def action_cursor_home(self) -> None:
        self._select_index(0)

    def action_cursor_end(self) -> None:
        runs = self._run_items()
        if runs:
            self._select_index(len(runs) - 1)

    def action_submit_selection(self) -> None:
        if self._selected_run_id is None:
            return
        self.post_message(self.RunRequested(self._selected_run_id))

    def _run_items(self) -> tuple[RunSummaryView, ...]:
        if self._runs is None:
            return ()
        return self._runs.runs

    def _reconcile_selection(self, *, preferred_run_id: str | None = None) -> None:
        previous = self._selected_run_id
        runs = self._run_items()
        if not runs:
            self._selected_run_id = None
        else:
            run_ids = {run.run_id for run in runs}
            if preferred_run_id is not None and preferred_run_id in run_ids:
                self._selected_run_id = preferred_run_id
            elif self._selected_run_id not in run_ids:
                self._selected_run_id = runs[0].run_id
        if previous != self._selected_run_id and self.is_mounted:
            self.post_message(self.SelectionChanged(self._selected_run_id))

    def _selected_index(self) -> int | None:
        if self._selected_run_id is None:
            return None
        for index, run in enumerate(self._run_items()):
            if run.run_id == self._selected_run_id:
                return index
        return None

    def _move_selection(self, delta: int) -> None:
        runs = self._run_items()
        if not runs:
            return
        current_index = self._selected_index()
        if current_index is None:
            new_index = 0 if delta >= 0 else len(runs) - 1
        else:
            new_index = min(max(current_index + delta, 0), len(runs) - 1)
        self._select_index(new_index)

    def _select_index(self, index: int) -> None:
        runs = self._run_items()
        if not runs:
            return
        bounded_index = min(max(index, 0), len(runs) - 1)
        run_id = runs[bounded_index].run_id
        if run_id == self._selected_run_id:
            return
        self._selected_run_id = run_id
        if self.is_mounted:
            self._render_state()
            self.post_message(self.SelectionChanged(run_id))

    def _render_state(self) -> None:
        switcher = self.query_one("#runs-mode-switcher", ContentSwitcher)
        switcher.current = "runs-debug" if self._display_mode is DisplayMode.DEBUG else "runs-operator"
        self.query_one("#runs-debug", Static).update(self._render_debug_text())
        if self._display_mode is DisplayMode.DEBUG:
            return
        self._render_operator_surface()

    def _render_operator_surface(self) -> None:
        if self._runs is None:
            self._update_section("runs-status", "Waiting for the runs snapshot.", self._failure_operator_detail(has_snapshot=False))
            self._update_metric("runs-recent", "--", "no run snapshot")
            self._update_metric("runs-flagged", "--", "alerts unavailable")
            self._update_metric("runs-scanned", "--", "scan timestamp unavailable")
            self._update_section("runs-request", "No requested run", "no request context yet")
            self._set_run_items(headline="No runs visible", detail="recent run snapshot not loaded")
            self._update_section("runs-actions", "Waiting for run history", "open debug for hashes, routes, and provenance detail")
            return

        runs = self._runs.runs
        failure_count = sum(1 for run in runs if run_operator_alert(run))
        self._update_section(
            "runs-status",
            "Snapshot ready" if self._failure is None else "Refresh degraded",
            self._failure_operator_detail(has_snapshot=True) if self._failure is not None else "recent run drilldown remains available",
        )
        self._update_metric("runs-recent", str(len(runs)), "visible run summaries")
        self._update_metric("runs-flagged", str(failure_count), "issues or warnings")
        self._update_metric("runs-scanned", format_timestamp(self._runs.scanned_at), "latest scan")
        if self._requested_run_id is not None and self._requested_run_id not in {run.run_id for run in runs}:
            self._update_section(
                "runs-request",
                f"Missing: {compact_run_label(self._requested_run_id)}",
                "requested run is not in the current recent-runs list",
            )
        elif self._requested_run_id is not None:
            self._update_section(
                "runs-request",
                compact_run_label(self._requested_run_id),
                "requested run is visible in the recent list",
            )
        else:
            self._update_section("runs-request", "No requested run", "selection follows the visible recent-runs list")

        if not runs:
            self._set_run_items(headline="No run artifacts are visible yet.", detail="recent run directory is empty")
            self._update_section("runs-actions", "Waiting for runs", "Enter opens concise provenance detail when a run is visible")
            return

        self._render_runs_table(runs)
        self._set_run_items(headline=f"{len(runs)} recent runs", detail="selected run opens concise provenance detail")
        self._update_section("runs-actions", "Up/Down select, Enter opens detail", "flagged runs stay visible in operator mode")

    def _update_metric(self, suffix: str, value: str, meta: str) -> None:
        self.query_one(f"#{suffix}-value", Static).update(value)
        self.query_one(f"#{suffix}-meta", Static).update(meta)

    def _update_section(self, suffix: str, headline: str, detail: str) -> None:
        self.query_one(f"#{suffix}-headline", Static).update(headline)
        self.query_one(f"#{suffix}-detail", Static).update(detail)

    def _set_run_items(self, *, headline: str, detail: str) -> None:
        self.query_one("#runs-list-headline", Static).update(headline)
        self.query_one("#runs-list-detail", Static).update(detail)
        if self._runs is None or not self._run_items():
            self._render_runs_table(())

    def _render_runs_table(self, runs: tuple[RunSummaryView, ...]) -> None:
        table = self.query_one("#runs-table", DataTable)
        table.clear(columns=False)
        if not runs:
            table.add_row("--", "No recent runs", "", "", "", key="__runs-empty__")
            table.move_cursor(row=0, column=0, animate=False, scroll=False)
            return
        for run in runs:
            selection = (run.selection_ref or "").split(":", maxsplit=1)[-1] if run.selection_ref else "none"
            latest_when = format_timestamp(run.latest_transition_at) if run.latest_transition_at is not None else "--"
            status = run.latest_status or run.latest_transition_label or "unknown"
            table.add_row(
                run_operator_summary_lines(run)[0].split(maxsplit=1)[0],
                compact_run_label(run.run_id),
                selection,
                latest_when,
                " ".join(status.lower().split()),
                key=run.run_id,
            )
        self._sync_runs_table_cursor(scroll=False)

    def _sync_runs_table_cursor(self, *, scroll: bool) -> None:
        table = self.query_one("#runs-table", DataTable)
        if self._selected_run_id is None:
            if table.row_count:
                table.move_cursor(row=0, column=0, animate=False, scroll=scroll)
            return
        try:
            row_index = table.get_row_index(self._selected_run_id)
        except Exception:
            if table.row_count:
                table.move_cursor(row=0, column=0, animate=False, scroll=scroll)
            return
        table.move_cursor(row=row_index, column=0, animate=False, scroll=scroll)

    def _failure_operator_detail(self, *, has_snapshot: bool) -> str:
        if self._failure is None:
            return ""
        qualifier = "showing last known snapshot" if has_snapshot else "no snapshot available"
        return f"{qualifier} | {collapse_operator_text(self._failure.message)} | open debug for technical detail"

    def _render_text(self) -> str:
        if self._display_mode is DisplayMode.DEBUG:
            return self._render_debug_text()
        return self._render_operator_text()

    def _render_operator_text(self) -> str:
        lines: list[str] = []
        append_panel_failure_lines(
            lines,
            panel_label="RUNS",
            failure=self._failure,
            has_snapshot=self._runs is not None,
            display_mode=DisplayMode.OPERATOR,
        )
        if self._runs is None:
            lines.append("Waiting for the runs snapshot.")
            return "\n".join(lines)

        runs = self._runs.runs
        failure_count = sum(1 for run in runs if run_operator_alert(run))
        lines.append(
            "SUMMARY "
            f"recent {len(runs)}"
            f" | flagged {failure_count}"
            f" | scanned {format_timestamp(self._runs.scanned_at)}"
        )
        if self._requested_run_id is not None and self._requested_run_id not in {run.run_id for run in runs}:
            lines.append(
                "REQUEST requested run "
                f"{compact_run_label(self._requested_run_id)} is not in the current recent-runs list"
            )
        if not runs:
            lines.append("")
            lines.append("No run artifacts are visible yet.")
            return "\n".join(lines)

        for index, run in enumerate(runs, start=1):
            prefix = ">" if run.run_id == self._selected_run_id else " "
            header, detail = run_operator_summary_lines(run)
            lines.append(f"{prefix} {index:>2}. {header} | {detail}")
            alert = run_operator_alert(run)
            if alert:
                lines.append(f"    FAIL   {alert}")
        lines.append("")
        lines.append("NEXT    Up/Down select | Enter detail")
        return "\n".join(lines)

    def _render_debug_text(self) -> str:
        lines: list[str] = []
        append_panel_failure_lines(
            lines,
            panel_label="RUNS",
            failure=self._failure,
            has_snapshot=self._runs is not None,
            display_mode=DisplayMode.DEBUG,
        )
        if self._runs is None:
            lines.append("Waiting for the runs snapshot.")
            return "\n".join(lines)

        runs = self._runs.runs
        lines.append(
            "RECENT  "
            f"{len(runs)} runs"
            f" | scanned {format_timestamp(self._runs.scanned_at)}"
            f" | source {self._runs.runs_dir}"
        )
        lines.append("DETAIL  Up/Down move selection. Enter opens concise provenance detail.")
        if self._requested_run_id is not None and self._requested_run_id not in {run.run_id for run in runs}:
            lines.append(f"REQUEST requested run {self._requested_run_id} is not in the current recent-runs list")
        if not runs:
            lines.append("")
            lines.append("No run artifacts are visible yet.")
            return "\n".join(lines)

        for index, run in enumerate(runs, start=1):
            prefix = ">" if run.run_id == self._selected_run_id else " "
            header, detail = run_summary_lines(run)
            lines.append(f"{prefix} {index:>2}. {header}")
            lines.append(f"    {detail}")
        return "\n".join(lines)


__all__ = ["RunsPanel"]
