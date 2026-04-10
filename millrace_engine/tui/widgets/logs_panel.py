"""Structured runtime logs panel for the Millrace TUI shell."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widgets import ContentSwitcher, DataTable, Static

from ..formatting import compact_run_label, format_short_timestamp, format_timestamp
from ..models import (
    DisplayMode,
    EventLogView,
    GatewayFailure,
    RuntimeEventIdentity,
    RuntimeEventView,
    runtime_event_identity,
)
from .progressive_disclosure import (
    append_panel_failure_lines,
    collapse_operator_text,
)

_ALL_FILTER = "all"
_EVENT_TABLE_EMPTY_KEY = "__logs-empty__"
_ARTIFACT_TABLE_EMPTY_KEY = "__logs-artifacts-empty__"
_MAX_ARTIFACT_ROWS = 24


@dataclass(frozen=True, slots=True)
class ArtifactEntry:
    relative_path: str
    kind: str
    detail: str
    full_path: str


def _event_priority(event: RuntimeEventView) -> str:
    text = f"{event.event_type} {event.summary}".lower()
    if any(token in text for token in ("fail", "error", "block", "denied", "exception", "panic")):
        return "ALERT"
    if any(token in text for token in ("warn", "degrad", "stale", "timeout", "retry", "pause", "stop")):
        return "WARN"
    return "INFO"


def _event_state_class(event: RuntimeEventView) -> str:
    priority = _event_priority(event)
    if priority == "ALERT":
        return "state-fail"
    if priority == "WARN":
        return "state-warn"
    return "state-ok"


def _operator_event_summary(event: RuntimeEventView) -> str:
    summary = collapse_operator_text(event.summary or event.event_type, max_parts=2, max_length=68)
    run_fragment = f" | run {compact_run_label(event.run_id)}" if event.run_id else ""
    return f"{event.event_type} | {summary}{run_fragment}"


def _format_artifact_size(path: Path) -> str:
    if path.is_dir():
        return "directory"
    try:
        size = path.stat().st_size
    except OSError:
        return "unavailable"
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def _artifact_entries_for_root(root: Path) -> tuple[ArtifactEntry, ...]:
    if not root.exists() or not root.is_dir():
        return ()

    collected: list[ArtifactEntry] = []
    for path in sorted(root.rglob("*"), key=lambda item: (not item.is_dir(), item.as_posix().lower())):
        if len(collected) >= _MAX_ARTIFACT_ROWS:
            break
        relative = path.relative_to(root).as_posix()
        if not relative:
            continue
        kind = "dir" if path.is_dir() else path.suffix.lstrip(".") or "file"
        collected.append(
            ArtifactEntry(
                relative_path=relative + ("/" if path.is_dir() else ""),
                kind=kind,
                detail=_format_artifact_size(path),
                full_path=path.as_posix(),
            )
        )
    return tuple(collected)


class LogsPanel(Static):
    """Focusable log view with structured event and artifact inspection."""

    can_focus = True
    BINDINGS = (
        Binding("up", "cursor_up", show=False),
        Binding("down", "cursor_down", show=False),
        Binding("home", "cursor_home", show=False),
        Binding("end", "cursor_end", show=False),
        Binding("enter", "submit_selection", show=False),
        Binding("f", "toggle_follow", show=False),
        Binding("tab", "toggle_focus_surface", show=False),
        Binding("ctrl+left", "previous_source_filter", show=False),
        Binding("ctrl+right", "next_source_filter", show=False),
        Binding("ctrl+up", "previous_event_type_filter", show=False),
        Binding("ctrl+down", "next_event_type_filter", show=False),
    )

    class RunRequested(Message):
        """Posted when the selected event carries a run id and the operator presses enter."""

        bubble = True

        def __init__(self, run_id: str) -> None:
            super().__init__()
            self.run_id = run_id

    class SelectionChanged(Message):
        """Posted when event or artifact selection changes."""

        bubble = True

        def __init__(self, event_key: RuntimeEventIdentity | None, artifact_path: str | None) -> None:
            super().__init__()
            self.event_key = event_key
            self.artifact_path = artifact_path

    def __init__(self, *, workspace_path: Path | None = None, id: str | None = None) -> None:
        super().__init__("", id=id, classes="panel-card", markup=False)
        self.border_title = "Logs"
        self._workspace_path = workspace_path
        self._events: EventLogView | None = None
        self._failure: GatewayFailure | None = None
        self._follow_mode = True
        self._source_filter = _ALL_FILTER
        self._event_type_filter = _ALL_FILTER
        self._selected_event_key: RuntimeEventIdentity | None = None
        self._selected_artifact_path: str | None = None
        self._display_mode: DisplayMode = DisplayMode.OPERATOR
        self._focus_surface = "events"
        self._artifact_entries: tuple[ArtifactEntry, ...] = ()
        self._artifact_root: str | None = None

    def compose(self) -> ComposeResult:
        with ContentSwitcher(initial="logs-operator", id="logs-mode-switcher"):
            with Vertical(id="logs-operator", classes="panel-mode-body"):
                yield self._section_card("logs-status", "Status")
                with Horizontal(classes="panel-metrics"):
                    yield self._metric_card("logs-mode", "Mode")
                    yield self._metric_card("logs-visible", "Visible")
                    yield self._metric_card("logs-alerts", "Alerts")
                yield self._section_card("logs-filters", "Filters")
                with Vertical(id="logs-events-card", classes="overview-card panel-section-card panel-list-card"):
                    yield Static("Events", classes="overview-card-label")
                    yield Static("--", id="logs-events-headline", classes="overview-card-headline")
                    yield Static("", id="logs-events-detail", classes="overview-card-detail")
                    yield DataTable(id="logs-table", classes="panel-data-table")
                    yield Static("", id="logs-events-focus", classes="overview-card-detail")
                yield self._section_card("logs-selection", "Selected event")
                with Vertical(id="logs-artifacts-card", classes="overview-card panel-section-card panel-list-card"):
                    yield Static("Artifacts", classes="overview-card-label")
                    yield Static("--", id="logs-artifacts-headline", classes="overview-card-headline")
                    yield Static("", id="logs-artifacts-detail", classes="overview-card-detail")
                    yield DataTable(id="logs-artifacts-table", classes="panel-data-table")
                    yield Static("", id="logs-artifacts-focus", classes="overview-card-detail")
                yield self._section_card("logs-actions", "Actions")
            yield Static("", id="logs-debug", classes="panel-debug-body")

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
    def follow_mode(self) -> bool:
        return self._follow_mode

    @property
    def source_filter(self) -> str:
        return self._source_filter

    @property
    def event_type_filter(self) -> str:
        return self._event_type_filter

    @property
    def selected_run_id(self) -> str | None:
        selected = self._selected_event()
        return None if selected is None else selected.run_id

    @property
    def selected_event(self) -> RuntimeEventView | None:
        return self._selected_event()

    @property
    def selected_event_key(self) -> RuntimeEventIdentity | None:
        return self._selected_event_key

    @property
    def selected_artifact_path(self) -> str | None:
        return self._selected_artifact_path

    @property
    def artifact_root(self) -> str | None:
        return self._artifact_root

    @property
    def focus_surface(self) -> str:
        return self._focus_surface

    @property
    def filtered_events(self) -> tuple[RuntimeEventView, ...]:
        return self._filtered_events()

    def on_mount(self) -> None:
        event_table = self.query_one("#logs-table", DataTable)
        event_table.cursor_type = "row"
        event_table.zebra_stripes = True
        event_table.add_column("Time", key="time", width=9)
        event_table.add_column("Lvl", key="level", width=6)
        event_table.add_column("Source", key="source", width=12)
        event_table.add_column("Event", key="event", width=26)
        event_table.add_column("Run", key="run", width=18)
        event_table.add_column("Summary", key="summary")

        artifact_table = self.query_one("#logs-artifacts-table", DataTable)
        artifact_table.cursor_type = "row"
        artifact_table.zebra_stripes = True
        artifact_table.add_column("Kind", key="kind", width=8)
        artifact_table.add_column("Path", key="path", width=36)
        artifact_table.add_column("Detail", key="detail")
        self._render_state()

    @on(DataTable.RowHighlighted, "#logs-table")
    def _handle_logs_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        key = event.row_key.value
        if key == _EVENT_TABLE_EMPTY_KEY:
            return
        next_key = self._event_key_from_token(key)
        if next_key == self._selected_event_key:
            return
        self._selected_event_key = next_key
        self._focus_surface = "events"
        self._reconcile_artifacts()
        self._update_focus_labels()
        self.post_message(self.SelectionChanged(self._selected_event_key, self._selected_artifact_path))

    @on(DataTable.RowSelected, "#logs-table")
    def _handle_logs_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.row_key.value == _EVENT_TABLE_EMPTY_KEY:
            return
        self.action_submit_selection()

    @on(DataTable.RowHighlighted, "#logs-artifacts-table")
    def _handle_artifact_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        path = event.row_key.value
        if path == _ARTIFACT_TABLE_EMPTY_KEY:
            return
        if path == self._selected_artifact_path:
            return
        self._selected_artifact_path = path
        self._focus_surface = "artifacts"
        self._update_focus_labels()
        self.post_message(self.SelectionChanged(self._selected_event_key, self._selected_artifact_path))

    @on(DataTable.RowSelected, "#logs-artifacts-table")
    def _handle_artifact_row_selected(self, event: DataTable.RowSelected) -> None:
        path = event.row_key.value
        if path == _ARTIFACT_TABLE_EMPTY_KEY:
            return
        if path != self._selected_artifact_path:
            self._selected_artifact_path = path
            self._focus_surface = "artifacts"
            self._update_focus_labels()
            self.post_message(self.SelectionChanged(self._selected_event_key, self._selected_artifact_path))

    def show_snapshot(
        self,
        events: EventLogView | None,
        *,
        failure: GatewayFailure | None = None,
        display_mode: DisplayMode = DisplayMode.OPERATOR,
    ) -> None:
        previous_event = self._selected_event_key
        previous_artifact = self._selected_artifact_path
        self._events = events
        self._failure = failure
        self._display_mode = display_mode
        self._reconcile_filters()
        self._reconcile_selection(updated=True)
        self._reconcile_artifacts()
        if self.is_mounted:
            self._render_state()
        if previous_event != self._selected_event_key or previous_artifact != self._selected_artifact_path:
            self.post_message(self.SelectionChanged(self._selected_event_key, self._selected_artifact_path))

    def summary_text(self) -> str:
        if self._display_mode is DisplayMode.DEBUG:
            return self._render_debug_text()
        return self._render_operator_text()

    def set_source_filter(self, source: str | None) -> None:
        self._source_filter = self._normalized_filter(source)
        self._reconcile_filters()
        self._reconcile_selection(updated=False)
        self._reconcile_artifacts()
        if self.is_mounted:
            self._render_state()
        self.post_message(self.SelectionChanged(self._selected_event_key, self._selected_artifact_path))

    def set_event_type_filter(self, event_type: str | None) -> None:
        self._event_type_filter = self._normalized_filter(event_type)
        self._reconcile_filters()
        self._reconcile_selection(updated=False)
        self._reconcile_artifacts()
        if self.is_mounted:
            self._render_state()
        self.post_message(self.SelectionChanged(self._selected_event_key, self._selected_artifact_path))

    def action_cursor_up(self) -> None:
        if self._focus_surface == "artifacts" and self._artifact_entries:
            self._move_artifact_selection(-1)
            return
        self._move_selection(-1)

    def action_cursor_down(self) -> None:
        if self._focus_surface == "artifacts" and self._artifact_entries:
            self._move_artifact_selection(1)
            return
        self._move_selection(1)

    def action_cursor_home(self) -> None:
        if self._focus_surface == "artifacts" and self._artifact_entries:
            self._select_artifact_index(0)
            return
        self._select_index(0)

    def action_cursor_end(self) -> None:
        if self._focus_surface == "artifacts" and self._artifact_entries:
            self._select_artifact_index(len(self._artifact_entries) - 1)
            return
        filtered = self._filtered_events()
        if filtered:
            self._select_index(len(filtered) - 1)

    def action_toggle_follow(self) -> None:
        self._follow_mode = not self._follow_mode
        self._focus_surface = "events"
        self._reconcile_selection(updated=False)
        self._reconcile_artifacts()
        if self.is_mounted:
            self._render_state()
        self.post_message(self.SelectionChanged(self._selected_event_key, self._selected_artifact_path))

    def action_jump_to_live(self) -> None:
        if self._follow_mode and self._selected_event() is not None:
            return
        self._follow_mode = True
        self._focus_surface = "events"
        self._reconcile_selection(updated=True)
        self._reconcile_artifacts()
        if self.is_mounted:
            self._render_state()
        self.post_message(self.SelectionChanged(self._selected_event_key, self._selected_artifact_path))

    def action_toggle_focus_surface(self) -> None:
        if not self._artifact_entries:
            self._focus_surface = "events"
        elif self._focus_surface == "events":
            self._focus_surface = "artifacts"
        else:
            self._focus_surface = "events"
        if self.is_mounted:
            self._render_state()

    def action_previous_source_filter(self) -> None:
        self._cycle_source_filter(-1)

    def action_next_source_filter(self) -> None:
        self._cycle_source_filter(1)

    def action_previous_event_type_filter(self) -> None:
        self._cycle_event_type_filter(-1)

    def action_next_event_type_filter(self) -> None:
        self._cycle_event_type_filter(1)

    def action_submit_selection(self) -> None:
        if self._focus_surface != "events":
            return
        selected = self._selected_event()
        if selected is None or selected.run_id is None:
            return
        self.post_message(self.RunRequested(selected.run_id))

    def _normalized_filter(self, value: str | None) -> str:
        normalized = " ".join((value or "").split())
        return normalized or _ALL_FILTER

    def _all_events(self) -> tuple[RuntimeEventView, ...]:
        if self._events is None:
            return ()
        return self._events.events

    def _source_filtered_events(self) -> tuple[RuntimeEventView, ...]:
        events = self._all_events()
        if self._source_filter == _ALL_FILTER:
            return events
        return tuple(event for event in events if event.source == self._source_filter)

    def _filtered_events(self) -> tuple[RuntimeEventView, ...]:
        events = self._source_filtered_events()
        if self._event_type_filter == _ALL_FILTER:
            return events
        return tuple(event for event in events if event.event_type == self._event_type_filter)

    def _source_options(self) -> tuple[str, ...]:
        sources = sorted({event.source for event in self._all_events()})
        return (_ALL_FILTER, *sources)

    def _event_type_options(self) -> tuple[str, ...]:
        event_types = sorted({event.event_type for event in self._source_filtered_events()})
        return (_ALL_FILTER, *event_types)

    def _event_token(self, event: RuntimeEventView) -> str:
        return "\x1f".join(runtime_event_identity(event)[:6]) + "\x1e" + "\x1f".join(
            f"{item.key}={item.value}" for item in event.payload
        )

    def _event_key_from_token(self, token: str) -> RuntimeEventIdentity:
        head, _, payload_text = token.partition("\x1e")
        parts = tuple(head.split("\x1f"))
        payload: tuple[tuple[str, str], ...] = ()
        if payload_text:
            payload = tuple(
                tuple(item.split("=", maxsplit=1))  # type: ignore[arg-type]
                for item in payload_text.split("\x1f")
                if item
            )
        return (
            parts[0],
            parts[1],
            parts[2],
            parts[3],
            parts[4],
            parts[5],
            payload,
        )

    def _selected_event(self) -> RuntimeEventView | None:
        selected_key = self._selected_event_key
        if selected_key is None:
            return None
        for event in self._filtered_events():
            if runtime_event_identity(event) == selected_key:
                return event
        return None

    def _selected_index(self, filtered: tuple[RuntimeEventView, ...]) -> int | None:
        selected_key = self._selected_event_key
        if selected_key is None:
            return None
        for index, event in enumerate(filtered):
            if runtime_event_identity(event) == selected_key:
                return index
        return None

    def _selected_artifact_index(self) -> int | None:
        if self._selected_artifact_path is None:
            return None
        for index, entry in enumerate(self._artifact_entries):
            if entry.relative_path == self._selected_artifact_path:
                return index
        return None

    def _reconcile_filters(self) -> None:
        if self._source_filter not in self._source_options():
            self._source_filter = _ALL_FILTER
        if self._event_type_filter not in self._event_type_options():
            self._event_type_filter = _ALL_FILTER

    def _reconcile_selection(self, *, updated: bool) -> None:
        filtered = self._filtered_events()
        if not filtered:
            self._selected_event_key = None
            return
        if self._follow_mode or self._selected_event_key is None:
            self._selected_event_key = runtime_event_identity(filtered[-1])
            return
        selected = self._selected_event()
        if selected is not None:
            return
        self._selected_event_key = runtime_event_identity(filtered[-1] if updated else filtered[0])

    def _reconcile_artifacts(self) -> None:
        selected = self._selected_event()
        run_id = None if selected is None else selected.run_id
        self._artifact_root = None
        self._artifact_entries = ()
        if self._workspace_path is not None and run_id:
            candidate = self._workspace_path / "agents" / "runs" / run_id
            self._artifact_root = candidate.as_posix()
            self._artifact_entries = _artifact_entries_for_root(candidate)
        if not self._artifact_entries:
            self._selected_artifact_path = None
            self._focus_surface = "events"
            return
        if self._selected_artifact_path not in {entry.relative_path for entry in self._artifact_entries}:
            self._selected_artifact_path = self._artifact_entries[0].relative_path

    def _move_selection(self, delta: int) -> None:
        filtered = self._filtered_events()
        if not filtered:
            return
        current_index = self._selected_index(filtered)
        if current_index is None:
            new_index = len(filtered) - 1 if delta < 0 else 0
        else:
            new_index = min(max(current_index + delta, 0), len(filtered) - 1)
        self._select_index(new_index)

    def _select_index(self, index: int) -> None:
        filtered = self._filtered_events()
        if not filtered:
            return
        bounded_index = min(max(index, 0), len(filtered) - 1)
        next_key = runtime_event_identity(filtered[bounded_index])
        if next_key == self._selected_event_key and self._focus_surface == "events":
            return
        self._selected_event_key = next_key
        self._focus_surface = "events"
        self._reconcile_artifacts()
        if self.is_mounted:
            self._render_state()
        self.post_message(self.SelectionChanged(self._selected_event_key, self._selected_artifact_path))

    def _move_artifact_selection(self, delta: int) -> None:
        if not self._artifact_entries:
            return
        current_index = self._selected_artifact_index()
        if current_index is None:
            new_index = len(self._artifact_entries) - 1 if delta < 0 else 0
        else:
            new_index = min(max(current_index + delta, 0), len(self._artifact_entries) - 1)
        self._select_artifact_index(new_index)

    def _select_artifact_index(self, index: int) -> None:
        if not self._artifact_entries:
            return
        bounded_index = min(max(index, 0), len(self._artifact_entries) - 1)
        next_path = self._artifact_entries[bounded_index].relative_path
        if next_path == self._selected_artifact_path and self._focus_surface == "artifacts":
            return
        self._selected_artifact_path = next_path
        self._focus_surface = "artifacts"
        if self.is_mounted:
            self._render_state()
        self.post_message(self.SelectionChanged(self._selected_event_key, self._selected_artifact_path))

    def _cycle_source_filter(self, delta: int) -> None:
        options = self._source_options()
        index = options.index(self._source_filter) if self._source_filter in options else 0
        self._source_filter = options[(index + delta) % len(options)]
        if self._event_type_filter not in self._event_type_options():
            self._event_type_filter = _ALL_FILTER
        self._focus_surface = "events"
        self._reconcile_selection(updated=False)
        self._reconcile_artifacts()
        if self.is_mounted:
            self._render_state()
        self.post_message(self.SelectionChanged(self._selected_event_key, self._selected_artifact_path))

    def _cycle_event_type_filter(self, delta: int) -> None:
        options = self._event_type_options()
        index = options.index(self._event_type_filter) if self._event_type_filter in options else 0
        self._event_type_filter = options[(index + delta) % len(options)]
        self._focus_surface = "events"
        self._reconcile_selection(updated=False)
        self._reconcile_artifacts()
        if self.is_mounted:
            self._render_state()
        self.post_message(self.SelectionChanged(self._selected_event_key, self._selected_artifact_path))

    def _render_state(self) -> None:
        switcher = self.query_one("#logs-mode-switcher", ContentSwitcher)
        switcher.current = "logs-debug" if self._display_mode is DisplayMode.DEBUG else "logs-operator"
        self.query_one("#logs-debug", Static).update(self._render_debug_text())
        if self._display_mode is DisplayMode.DEBUG:
            return
        self._render_operator_surface()

    def _render_operator_surface(self) -> None:
        if self._events is None:
            self._update_section("logs-status", "Waiting for the event stream.", self._failure_operator_detail(has_snapshot=False))
            self._update_metric("logs-mode", "--", "follow state unavailable")
            self._update_metric("logs-visible", "--", "event count unavailable")
            self._update_metric("logs-alerts", "--", "priority counts unavailable")
            self._update_section("logs-filters", "No active snapshot", "source and event-type filters will apply after refresh")
            self._set_event_items(headline="No events visible", detail="event log snapshot not loaded")
            self._update_section("logs-selection", "No event selected", "selection appears once filtered events are visible")
            self._set_artifact_items(headline="No artifacts visible", detail="artifact browsing appears for the selected run")
            self._update_section("logs-actions", "Waiting for logs", "follow, filters, run detail, and artifact browsing appear when events load")
            return

        filtered = self._filtered_events()
        selected = self._selected_event()
        alert_count = sum(1 for event in filtered if _event_priority(event) == "ALERT")
        warn_count = sum(1 for event in filtered if _event_priority(event) == "WARN")

        self._update_section(
            "logs-status",
            "Snapshot ready" if self._failure is None else "Refresh degraded",
            self._failure_operator_detail(has_snapshot=True)
            if self._failure is not None
            else "compact logs stay live while expanded mode remains optional",
        )
        self._update_metric(
            "logs-mode",
            "follow" if self._follow_mode else "frozen",
            f"focus {self._focus_surface} | source {self._source_filter} | type {self._event_type_filter}",
        )
        self._update_metric("logs-visible", str(len(filtered)), f"loaded {len(self._all_events())} | last {format_timestamp(self._events.last_loaded_at)}")
        self._update_metric("logs-alerts", f"{alert_count}/{warn_count}", "alert / warn priorities")
        self._update_section(
            "logs-filters",
            f"source {self._source_filter} | type {self._event_type_filter}",
            f"last batch {format_timestamp(self._events.last_loaded_at)}",
        )

        if not filtered:
            self._set_event_items(headline="No events match the active filters", detail="adjust source/type filters or wait for new events")
            self._update_section("logs-selection", "No event selected", "filtered view is empty")
            self._set_artifact_items(headline="No artifacts visible", detail="artifact browsing appears for the selected run")
            self._update_section("logs-actions", "Filters remain active", "Tab keeps focus on events because there is no selected run artifact surface")
            return

        event_detail = f"{len(filtered)} filtered events | focus {self._focus_surface}"
        if selected is not None:
            selected_index = self._selected_index(filtered)
            if selected_index is not None:
                event_detail = f"{selected_index + 1}/{len(filtered)} selected | focus {self._focus_surface}"
        self._set_event_items(headline="Recent runtime events", detail=event_detail)
        self._render_events_table(filtered)

        if selected is None:
            self._update_section("logs-selection", "No event selected", "focus content and move the cursor to inspect an event")
        else:
            selection_lines = [
                collapse_operator_text(selected.summary or selected.event_type, max_parts=2, max_length=92),
                f"{selected.source} | {_event_priority(selected)} | {selected.category or 'event'}",
            ]
            if selected.run_id:
                selection_lines.append(f"run {compact_run_label(selected.run_id)}")
            if selected.payload:
                selection_lines.append(
                    "payload " + ", ".join(f"{item.key}={item.value}" for item in selected.payload[:4])
                )
            self._update_section(
                "logs-selection",
                f"{selected.event_type} | {format_timestamp(selected.timestamp)}",
                " | ".join(selection_lines),
            )

        if self._artifact_entries:
            root_label = compact_run_label(selected.run_id or "") if selected is not None and selected.run_id else "run"
            self._set_artifact_items(
                headline=f"{len(self._artifact_entries)} visible for {root_label}",
                detail=self._artifact_root or "artifact root unavailable",
            )
        elif selected is not None and selected.run_id is not None:
            root = self._artifact_root or f"agents/runs/{selected.run_id}"
            self._set_artifact_items(headline="No readable artifacts for selected run", detail=root)
        else:
            self._set_artifact_items(headline="No artifacts visible", detail="select an event with a run id to inspect run artifacts")
        self._render_artifact_table(self._artifact_entries)

        action_detail = (
            "Up/Down browse the active surface | Tab switches between events and artifacts | "
            "Enter opens run detail from the selected event | f toggles follow"
        )
        self._update_section("logs-actions", "Browse and inspect", action_detail)

    def _update_metric(self, suffix: str, value: str, meta: str) -> None:
        self.query_one(f"#{suffix}-value", Static).update(value)
        self.query_one(f"#{suffix}-meta", Static).update(meta)

    def _update_section(self, suffix: str, headline: str, detail: str) -> None:
        self.query_one(f"#{suffix}-headline", Static).update(headline)
        self.query_one(f"#{suffix}-detail", Static).update(detail)

    def _set_event_items(self, *, headline: str, detail: str) -> None:
        self.query_one("#logs-events-headline", Static).update(headline)
        self.query_one("#logs-events-detail", Static).update(detail)

    def _set_artifact_items(self, *, headline: str, detail: str) -> None:
        self.query_one("#logs-artifacts-headline", Static).update(headline)
        self.query_one("#logs-artifacts-detail", Static).update(detail)

    def _render_events_table(self, events: tuple[RuntimeEventView, ...]) -> None:
        table = self.query_one("#logs-table", DataTable)
        table.clear(columns=False)
        if not events:
            table.add_row("--", "--", "No events", "", "", "", key=_EVENT_TABLE_EMPTY_KEY)
            table.move_cursor(row=0, column=0, animate=False, scroll=False)
            self._update_focus_labels()
            return
        for event in events:
            table.add_row(
                format_short_timestamp(event.timestamp),
                _event_priority(event),
                event.source,
                collapse_operator_text(event.event_type, max_parts=1, max_length=25),
                compact_run_label(event.run_id) if event.run_id else "--",
                collapse_operator_text(event.summary or event.event_type, max_parts=2, max_length=56),
                key=self._event_token(event),
                label=_event_state_class(event),
            )
        self._sync_logs_table_cursor(scroll=False)
        self._update_focus_labels()

    def _render_artifact_table(self, entries: tuple[ArtifactEntry, ...]) -> None:
        table = self.query_one("#logs-artifacts-table", DataTable)
        table.clear(columns=False)
        if not entries:
            table.add_row("--", "No artifacts", "read-only artifact browsing appears for the selected run", key=_ARTIFACT_TABLE_EMPTY_KEY)
            table.move_cursor(row=0, column=0, animate=False, scroll=False)
            self._update_focus_labels()
            return
        for entry in entries:
            table.add_row(entry.kind, entry.relative_path, entry.detail, key=entry.relative_path)
        self._sync_artifact_table_cursor(scroll=False)
        self._update_focus_labels()

    def _sync_logs_table_cursor(self, *, scroll: bool) -> None:
        table = self.query_one("#logs-table", DataTable)
        filtered = self._filtered_events()
        selected_index = self._selected_index(filtered)
        if selected_index is None:
            if table.row_count:
                table.move_cursor(row=0, column=0, animate=False, scroll=scroll)
            return
        table.move_cursor(row=selected_index, column=0, animate=False, scroll=scroll)

    def _sync_artifact_table_cursor(self, *, scroll: bool) -> None:
        table = self.query_one("#logs-artifacts-table", DataTable)
        selected_index = self._selected_artifact_index()
        if selected_index is None:
            if table.row_count:
                table.move_cursor(row=0, column=0, animate=False, scroll=scroll)
            return
        table.move_cursor(row=selected_index, column=0, animate=False, scroll=scroll)

    def _update_focus_labels(self) -> None:
        selected = self._selected_event()
        if selected is None:
            event_text = "event focus empty"
        else:
            event_text = (
                f"{'ACTIVE' if self._focus_surface == 'events' else 'linked'} | "
                f"{selected.event_type} | {_event_priority(selected)}"
            )
        self.query_one("#logs-events-focus", Static).update(event_text)

        if not self._artifact_entries:
            artifact_text = "artifact focus unavailable"
        else:
            selected_artifact = next(
                (entry for entry in self._artifact_entries if entry.relative_path == self._selected_artifact_path),
                self._artifact_entries[0],
            )
            artifact_text = (
                f"{'ACTIVE' if self._focus_surface == 'artifacts' else 'linked'} | "
                f"{selected_artifact.relative_path} | {selected_artifact.detail}"
            )
        self.query_one("#logs-artifacts-focus", Static).update(artifact_text)

    def _failure_operator_detail(self, *, has_snapshot: bool) -> str:
        if self._failure is None:
            return ""
        if has_snapshot:
            return collapse_operator_text(self._failure.message, max_parts=2, max_length=88)
        return "open debug once a snapshot is available for deeper gateway detail"

    def _render_operator_text(self) -> str:
        lines: list[str] = []
        append_panel_failure_lines(
            lines,
            panel_label="LOGS",
            failure=self._failure,
            has_snapshot=self._events is not None,
            display_mode=DisplayMode.OPERATOR,
        )
        if self._events is None:
            lines.append("Waiting for the event stream.")
            return "\n".join(lines)

        filtered = self._filtered_events()
        selected = self._selected_event()
        alert_count = sum(1 for event in filtered if _event_priority(event) == "ALERT")
        warn_count = sum(1 for event in filtered if _event_priority(event) == "WARN")
        lines.append(
            "SUMMARY "
            f"{'follow' if self._follow_mode else 'frozen'}"
            f" | focus {self._focus_surface}"
            f" | visible {len(filtered)}"
            f" | alert {alert_count}"
            f" | warn {warn_count}"
        )
        lines.append(
            "FILTER  "
            f"source {self._source_filter}"
            f" | type {self._event_type_filter}"
            f" | last batch {format_timestamp(self._events.last_loaded_at)}"
        )
        if selected is None:
            lines.append("FOCUS   none selected")
        else:
            selected_index = self._selected_index(filtered)
            position = "?" if selected_index is None else f"{selected_index + 1}/{len(filtered)}"
            lines.append(
                "FOCUS   "
                f"{position}"
                f" | {_event_priority(selected)}"
                f" | {_operator_event_summary(selected)}"
            )
            if selected.payload:
                lines.append(
                    "DETAIL  " + ", ".join(f"{item.key}={item.value}" for item in selected.payload[:4])
                )
        if self._artifact_root is not None:
            lines.append(
                "ARTIFACT "
                f"{len(self._artifact_entries)} visible"
                f" | root {self._artifact_root}"
            )
            if self._selected_artifact_path is not None:
                lines.append(f"PATH    {self._selected_artifact_path}")
        lines.append(
            "NEXT    Up/Down browse the active surface. Tab switches events/artifacts. "
            "Enter opens run detail from the selected event. f toggles follow."
        )
        if not filtered:
            lines.append("")
            lines.append("No events match the active filters.")
            return "\n".join(lines)

        lines.append("")
        for event in filtered[:6]:
            prefix = ">" if selected is not None and runtime_event_identity(event) == runtime_event_identity(selected) else " "
            lines.append(
                f"{prefix} {format_short_timestamp(event.timestamp)} "
                f"{_event_priority(event):<5} {_operator_event_summary(event)}"
            )
        return "\n".join(lines)

    def _render_debug_text(self) -> str:
        lines: list[str] = []
        append_panel_failure_lines(
            lines,
            panel_label="LOGS",
            failure=self._failure,
            has_snapshot=self._events is not None,
            display_mode=DisplayMode.DEBUG,
        )
        if self._events is None:
            lines.append("Waiting for the event stream.")
            return "\n".join(lines)

        filtered = self._filtered_events()
        selected = self._selected_event()
        lines.append(
            "MODE    "
            f"{'follow' if self._follow_mode else 'frozen'}"
            f" | focus {self._focus_surface}"
            f" | retained {len(self._all_events())}"
            f" | visible {len(filtered)}"
        )
        lines.append(
            "FILTER  "
            f"source {self._source_filter}"
            f" | type {self._event_type_filter}"
            f" | last batch {format_timestamp(self._events.last_loaded_at)}"
        )
        if selected is None:
            lines.append("SELECT  none")
        else:
            lines.append(
                "SELECT  "
                f"{selected.event_type}"
                f" | run {selected.run_id or 'none'}"
                f" | {selected.summary}"
            )
        if self._artifact_root is not None:
            lines.append(
                "FILES   "
                f"root {self._artifact_root}"
                f" | visible {len(self._artifact_entries)}"
                f" | selected {self._selected_artifact_path or 'none'}"
            )
        lines.append(
            "DEBUG   Up/Down browse the active surface. Tab switches events/artifacts. "
            "Enter opens run when available. Ctrl+Left/Right source. Ctrl+Up/Down type."
        )
        if not filtered:
            lines.append("")
            lines.append("No events match the active filters.")
            return "\n".join(lines)

        lines.append("")
        for event in filtered[:8]:
            is_selected = selected is not None and runtime_event_identity(event) == runtime_event_identity(selected)
            prefix = ">" if is_selected else " "
            run_fragment = f" | run {event.run_id}" if event.run_id else ""
            lines.append(
                f"{prefix} {format_short_timestamp(event.timestamp)} "
                f"{event.category:<3} {event.event_type} | {event.summary}{run_fragment}"
            )
            if event.payload:
                payload_text = ", ".join(f"{item.key}={item.value}" for item in event.payload)
                lines.append(f"    payload {payload_text}")
        return "\n".join(lines)


__all__ = ["LogsPanel"]
