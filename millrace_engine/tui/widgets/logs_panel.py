"""Structured runtime logs panel for the Millrace TUI shell."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widget import Widget
from textual.widgets import ContentSwitcher, Static

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
    append_operator_debug_hint,
    append_panel_failure_lines,
    collapse_operator_text,
)

_ALL_FILTER = "all"
_DISPLAY_LIMIT = 10
_OPERATOR_DISPLAY_LIMIT = 6


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


class LogsPanel(Static):
    """Focusable log view with local follow or freeze and basic filters."""

    can_focus = True
    BINDINGS = (
        Binding("up", "cursor_up", show=False),
        Binding("down", "cursor_down", show=False),
        Binding("home", "cursor_home", show=False),
        Binding("end", "cursor_end", show=False),
        Binding("enter", "submit_selection", show=False),
        Binding("f", "toggle_follow", show=False),
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

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__("", id=id, classes="panel-card", markup=False)
        self.border_title = "Logs"
        self._events: EventLogView | None = None
        self._failure: GatewayFailure | None = None
        self._follow_mode = True
        self._source_filter = _ALL_FILTER
        self._event_type_filter = _ALL_FILTER
        self._selected_event_key: RuntimeEventIdentity | None = None
        self._display_mode: DisplayMode = DisplayMode.OPERATOR

    def compose(self) -> ComposeResult:
        with ContentSwitcher(initial="logs-operator", id="logs-mode-switcher"):
            with Vertical(id="logs-operator", classes="panel-mode-body"):
                yield self._section_card("logs-status", "Status")
                with Horizontal(classes="panel-metrics"):
                    yield self._metric_card("logs-mode", "Mode")
                    yield self._metric_card("logs-visible", "Visible")
                    yield self._metric_card("logs-alerts", "Alerts")
                yield self._section_card("logs-filters", "Filters")
                yield self._section_card("logs-focus", "Selection")
                with Vertical(id="logs-list-card", classes="overview-card panel-section-card panel-list-card"):
                    yield Static("Events", classes="overview-card-label")
                    yield Static("--", id="logs-list-headline", classes="overview-card-headline")
                    yield Static("", id="logs-list-detail", classes="overview-card-detail")
                    yield Vertical(id="logs-list-items", classes="panel-item-stack")
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

    @staticmethod
    def _event_card(event: RuntimeEventView, *, selected: bool) -> Vertical:
        detail_fragments = [event.source, event.event_type, _event_priority(event)]
        if event.run_id:
            detail_fragments.append(f"run {compact_run_label(event.run_id)}")
        classes = f"overview-card panel-item-card {_event_state_class(event)}"
        if selected:
            classes += " is-selected"
        return Vertical(
            Static(
                f"{format_short_timestamp(event.timestamp)}  {collapse_operator_text(event.summary or event.event_type, max_parts=1, max_length=70)}",
                classes="panel-item-title",
            ),
            Static(" | ".join(detail_fragments), classes="panel-item-meta"),
            classes=classes,
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

    def on_mount(self) -> None:
        self._render_state()

    def show_snapshot(
        self,
        events: EventLogView | None,
        *,
        failure: GatewayFailure | None = None,
        display_mode: DisplayMode = DisplayMode.OPERATOR,
    ) -> None:
        self._events = events
        self._failure = failure
        self._display_mode = display_mode
        self._reconcile_filters()
        self._reconcile_selection(updated=True)
        if self.is_mounted:
            self._render_state()

    def summary_text(self) -> str:
        if self._display_mode is DisplayMode.DEBUG:
            return self._render_debug_text()
        return self._render_operator_text()

    def set_source_filter(self, source: str | None) -> None:
        self._source_filter = self._normalized_filter(source)
        self._reconcile_filters()
        self._reconcile_selection(updated=False)
        if self.is_mounted:
            self._render_state()

    def set_event_type_filter(self, event_type: str | None) -> None:
        self._event_type_filter = self._normalized_filter(event_type)
        self._reconcile_filters()
        self._reconcile_selection(updated=False)
        if self.is_mounted:
            self._render_state()

    def action_cursor_up(self) -> None:
        self._move_selection(-1)

    def action_cursor_down(self) -> None:
        self._move_selection(1)

    def action_cursor_home(self) -> None:
        self._select_index(0)

    def action_cursor_end(self) -> None:
        filtered = self._filtered_events()
        if filtered:
            self._select_index(len(filtered) - 1)

    def action_toggle_follow(self) -> None:
        self._follow_mode = not self._follow_mode
        self._reconcile_selection(updated=False)
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
        self._selected_event_key = runtime_event_identity(filtered[bounded_index])
        if self.is_mounted:
            self._render_state()

    def _cycle_source_filter(self, delta: int) -> None:
        options = self._source_options()
        index = options.index(self._source_filter) if self._source_filter in options else 0
        self._source_filter = options[(index + delta) % len(options)]
        if self._event_type_filter not in self._event_type_options():
            self._event_type_filter = _ALL_FILTER
        self._reconcile_selection(updated=False)
        if self.is_mounted:
            self._render_state()

    def _cycle_event_type_filter(self, delta: int) -> None:
        options = self._event_type_options()
        index = options.index(self._event_type_filter) if self._event_type_filter in options else 0
        self._event_type_filter = options[(index + delta) % len(options)]
        self._reconcile_selection(updated=False)
        if self.is_mounted:
            self._render_state()

    def _visible_window(self) -> tuple[tuple[RuntimeEventView, ...], int]:
        filtered = self._filtered_events()
        display_limit = _OPERATOR_DISPLAY_LIMIT if self._display_mode is DisplayMode.OPERATOR else _DISPLAY_LIMIT
        if len(filtered) <= display_limit:
            return filtered, 0

        selected_index = self._selected_index(filtered)
        if selected_index is None or self._follow_mode:
            start = len(filtered) - display_limit
        else:
            start = min(max(selected_index - display_limit + 1, 0), len(filtered) - display_limit)
        return filtered[start : start + display_limit], start

    def _render_state(self) -> None:
        switcher = self.query_one("#logs-mode-switcher", ContentSwitcher)
        switcher.current = "logs-debug" if self._display_mode is DisplayMode.DEBUG else "logs-operator"
        self.query_one("#logs-debug", Static).update(self._render_debug_text())
        if self._display_mode is DisplayMode.DEBUG:
            return
        self._render_operator_cards()

    def _render_operator_cards(self) -> None:
        if self._events is None:
            self._update_section("logs-status", "Waiting for the event stream.", self._failure_operator_detail(has_snapshot=False))
            self._update_metric("logs-mode", "--", "follow state unavailable")
            self._update_metric("logs-visible", "--", "event count unavailable")
            self._update_metric("logs-alerts", "--", "priority counts unavailable")
            self._update_section("logs-filters", "No active snapshot", "source and event-type filters will apply after refresh")
            self._update_section("logs-focus", "No event selected", "selection appears once filtered events are visible")
            self._set_event_items(headline="No events visible", detail="event log snapshot not loaded", items=())
            self._update_section("logs-actions", "Waiting for logs", "follow, filters, and run handoff will appear when events load")
            return

        filtered = self._filtered_events()
        selected = self._selected_event()
        window, start = self._visible_window()
        alert_count = sum(1 for event in filtered if _event_priority(event) == "ALERT")
        warn_count = sum(1 for event in filtered if _event_priority(event) == "WARN")

        self._update_section(
            "logs-status",
            "Snapshot ready" if self._failure is None else "Refresh degraded",
            self._failure_operator_detail(has_snapshot=True)
            if self._failure is not None
            else "recent runtime events remain visible and navigable",
        )
        self._update_metric("logs-mode", "follow" if self._follow_mode else "frozen", f"source {self._source_filter} | type {self._event_type_filter}")
        self._update_metric("logs-visible", str(len(filtered)), f"loaded {len(self._all_events())} | last {format_timestamp(self._events.last_loaded_at)}")
        self._update_metric("logs-alerts", f"{alert_count}/{warn_count}", "alert / warn priorities")
        self._update_section(
            "logs-filters",
            f"source {self._source_filter} | type {self._event_type_filter}",
            f"last batch {format_timestamp(self._events.last_loaded_at)}",
        )

        if selected is None:
            self._update_section("logs-focus", "No event selected", "follow mode will pin the newest visible event")
        else:
            selected_index = self._selected_index(filtered)
            position = "?" if selected_index is None else f"{selected_index + 1}/{len(filtered)}"
            self._update_section(
                "logs-focus",
                f"{position} | {_event_priority(selected)} | {selected.event_type}",
                collapse_operator_text(selected.summary or selected.event_type, max_parts=2, max_length=92),
            )

        if not filtered:
            self._set_event_items(headline="No events match the active filters", detail="adjust source/type filters or wait for new events", items=())
            self._update_section("logs-actions", "Follow and filters remain available", "open debug for payload context once matching events appear")
            return

        items = tuple(
            self._event_card(event, selected=selected is not None and runtime_event_identity(event) == runtime_event_identity(selected))
            for event in window
        )
        detail = f"showing {start + 1}-{start + len(window)} of {len(filtered)} filtered events" if len(window) != len(filtered) else f"{len(filtered)} filtered events"
        self._set_event_items(headline="Recent runtime events", detail=detail, items=items)

        if selected is not None and selected.run_id is not None:
            action_detail = (
                f"Enter opens run {compact_run_label(selected.run_id)} | "
                "f toggles follow | Ctrl+Arrows change filters"
            )
        else:
            action_detail = "Up/Down select | f toggles follow | Ctrl+Arrows change filters | open debug for payload detail"
        self._update_section("logs-actions", "Navigation and handoff ready", action_detail)

    def _update_metric(self, suffix: str, value: str, meta: str) -> None:
        self.query_one(f"#{suffix}-value", Static).update(value)
        self.query_one(f"#{suffix}-meta", Static).update(meta)

    def _update_section(self, suffix: str, headline: str, detail: str) -> None:
        self.query_one(f"#{suffix}-headline", Static).update(headline)
        self.query_one(f"#{suffix}-detail", Static).update(detail)

    def _set_event_items(self, *, headline: str, detail: str, items: tuple[Widget, ...]) -> None:
        self.query_one("#logs-list-headline", Static).update(headline)
        self.query_one("#logs-list-detail", Static).update(detail)
        container = self.query_one("#logs-list-items", Vertical)
        container.remove_children()
        if items:
            for item in items:
                container.mount(item)
            return
        container.mount(
            Vertical(
                Static("No event rows", classes="panel-item-title"),
                Static(detail, classes="panel-item-meta"),
                classes="overview-card panel-item-card panel-empty-card",
            )
        )

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
        lines.append(
            "NEXT    Up/Down select. Enter opens run when available. "
            "f toggles follow. Ctrl+Left/Right source. Ctrl+Up/Down type."
        )
        if not filtered:
            lines.append("")
            lines.append("No events match the active filters.")
            return "\n".join(lines)

        window, start = self._visible_window()
        if len(window) != len(filtered):
            lines.append("")
            lines.append(f"WINDOW  showing {start + 1}-{start + len(window)} of {len(filtered)} filtered events")
        lines.append("")
        for event in window:
            prefix = ">" if selected is not None and runtime_event_identity(event) == runtime_event_identity(selected) else " "
            lines.append(
                f"{prefix} {format_short_timestamp(event.timestamp)} "
                f"{_event_priority(event):<5} {_operator_event_summary(event)}"
            )
        if selected is not None and selected.run_id is not None:
            lines.append("")
            lines.append(
                f"RUN     press Enter to hand off {compact_run_label(selected.run_id)} "
                "to the run-detail workflow"
            )
        elif filtered:
            lines.append("")
            append_operator_debug_hint(lines, detail_hint="open debug for full payload and event context")
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
        lines.append(
            "DEBUG   Up/Down select. Enter opens run when available. "
            "f toggles follow. Ctrl+Left/Right source. Ctrl+Up/Down type."
        )
        if not filtered:
            lines.append("")
            lines.append("No events match the active filters.")
            return "\n".join(lines)

        window, start = self._visible_window()
        if len(window) != len(filtered):
            lines.append("")
            lines.append(f"WINDOW  showing {start + 1}-{start + len(window)} of {len(filtered)} filtered events")
        lines.append("")
        for event in window:
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
        if selected is not None and selected.run_id is not None:
            lines.append("")
            lines.append(f"RUN     press Enter to hand off {selected.run_id} to the run-detail workflow")
        return "\n".join(lines)


__all__ = ["LogsPanel"]
