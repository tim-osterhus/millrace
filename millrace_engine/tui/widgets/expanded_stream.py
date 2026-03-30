"""Shell-level expanded log-stream surface."""

from __future__ import annotations

from datetime import datetime, timezone

from textual.binding import Binding
from textual.events import MouseScrollDown, MouseScrollUp
from textual.widgets import Static

from ..formatting import render_runtime_event_debug_line
from ..models import DisplayMode, EventLogView, KeyValueView, RuntimeEventView


def _operator_timestamp(moment: datetime) -> str:
    normalized = moment if moment.tzinfo is not None else moment.replace(tzinfo=timezone.utc)
    return normalized.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _payload_map(items: tuple[KeyValueView, ...]) -> dict[str, str]:
    return {item.key: item.value for item in items}


def _first_present(payload: dict[str, str], *keys: str) -> str | None:
    for key in keys:
        value = " ".join(payload.get(key, "").split())
        if value:
            return value
    return None


def _labelize(value: str | None) -> str | None:
    normalized = " ".join((value or "").replace("_", " ").replace("-", " ").split())
    if not normalized:
        return None
    return normalized[0].upper() + normalized[1:]


def _run_prefix(payload: dict[str, str]) -> str:
    run_id = _first_present(payload, "run_id", "pause_run_id", "active_run_id", "parent_run_id")
    return f"Run {run_id}: " if run_id else ""


def render_runtime_event_operator_line(event: RuntimeEventView) -> str:
    """Render one operator-friendly narrated line from a structured runtime event."""

    payload = _payload_map(event.payload)
    event_type = event.event_type
    stage = _labelize(_first_present(payload, "stage", "node_id", "kind_id"))
    title = _first_present(payload, "title")
    task_id = _first_present(payload, "task_id", "active_task_id")
    reason = _first_present(payload, "policy_reason", "reason", "message", "failure_kind")
    status = _labelize(_first_present(payload, "status"))
    mode = _labelize(_first_present(payload, "mode", "runtime_mode", "configured_mode", "current_mode"))
    family = _labelize(_first_present(payload, "selected_family", "family", "queue_family"))
    command = _labelize(_first_present(payload, "command"))
    timestamp = _operator_timestamp(event.timestamp)
    prefix = _run_prefix(payload)

    if event_type == "engine.started":
        detail = f"Engine started in {mode.lower()} mode" if mode else "Engine started"
    elif event_type == "engine.stopped":
        paused = _first_present(payload, "paused")
        detail = "Engine stopped while paused" if paused == "true" else "Engine stopped"
    elif event_type == "engine.paused":
        detail = "Engine paused"
        if reason:
            detail += f" ({reason})"
    elif event_type == "engine.resumed":
        detail = "Engine resumed"
    elif event_type == "control.command.received":
        detail = f"Control received {command.lower()}" if command else "Control command received"
    elif event_type == "control.command.applied":
        applied = _first_present(payload, "applied", "ok")
        if applied == "false":
            detail = f"Control {command.lower()} did not apply" if command else "Control command did not apply"
        else:
            detail = f"Control applied {command.lower()}" if command else "Control command applied"
        if reason and applied == "false":
            detail += f" ({reason})"
    elif event_type == "execution.task.promoted":
        detail = f"Queue promoted {task_id}" if task_id else "Queue promoted work"
        if title:
            detail += f" ({title})"
    elif event_type == "execution.task.archived":
        detail = f"Run archived {task_id}" if task_id else "Run archived completed work"
        if title:
            detail += f" ({title})"
    elif event_type == "execution.task.quarantined":
        detail = f"Task quarantined {task_id}" if task_id else "Task quarantined"
        if title:
            detail += f" ({title})"
    elif event_type == "execution.stage.started":
        detail = f"{prefix}{stage or 'Stage'} started"
    elif event_type == "execution.stage.completed":
        detail = f"{prefix}{stage or 'Stage'} completed"
        if status:
            detail += f" ({status.lower()})"
    elif event_type == "execution.stage.failed":
        if status and status.lower() in {"blocked", "degraded"}:
            detail = f"{prefix}{stage or 'Stage'} {status.lower()}"
        else:
            detail = f"{prefix}{stage or 'Stage'} failed"
        if reason:
            detail += f" ({reason})"
    elif event_type == "execution.status.changed":
        detail = f"{prefix}Execution status -> {status}" if status else f"{prefix}Execution status changed"
    elif event_type == "execution.quickfix.attempt":
        detail = f"{prefix}Quickfix attempt for {stage.lower()}" if stage else f"{prefix}Quickfix attempt started"
    elif event_type == "execution.quickfix.exhausted":
        detail = f"{prefix}Quickfix exhausted"
        if reason:
            detail += f" ({reason})"
    elif event_type == "execution.backlog.empty":
        detail = "Execution idle; backlog empty"
    elif event_type == "config.changed":
        boundary = _labelize(_first_present(payload, "boundary"))
        detail = "Config change queued"
        if boundary:
            detail += f" for {boundary.lower()}"
    elif event_type == "config.applied":
        rollback = _first_present(payload, "rollback")
        detail = "Config rollback applied" if rollback == "true" else "Config applied"
        if reason:
            detail += f" ({reason})"
    elif event_type == "handoff.needs_research":
        detail = "Research wakeup: blocker handoff ready"
        if task_id:
            detail += f" for {task_id}"
    elif event_type == "handoff.backlog_empty_audit":
        detail = "Research wakeup: backlog-empty audit requested"
    elif event_type == "handoff.audit_requested":
        detail = "Research wakeup: audit requested"
    elif event_type == "handoff.backlog_repopulated":
        detail = "Queue wakeup: backlog repopulated"
    elif event_type == "handoff.idea_submitted":
        detail = "Research wakeup: idea queued"
    elif event_type == "research.received":
        detail = f"Research received {family.lower()} work" if family else "Research received new work"
    elif event_type == "research.deferred":
        pending = _first_present(payload, "pending_count")
        detail = f"Research deferred {family.lower()} work" if family else "Research deferred work"
        if pending:
            detail += f" ({pending} pending)"
    elif event_type == "research.scan.completed":
        detail = "Research scan complete"
        if family:
            detail += f"; {family.lower()} queue ready"
    elif event_type == "research.mode.selected":
        detail = "Research dispatch selected"
        if family:
            detail += f" {family.lower()}"
        if mode:
            detail += f" in {mode.lower()} mode"
    elif event_type == "research.dispatch.compiled":
        detail = f"Research dispatch compiled for {family.lower()}" if family else "Research dispatch compiled"
    elif event_type == "research.checkpoint.resumed":
        detail = f"Research resumed {family.lower()} checkpoint" if family else "Research checkpoint resumed"
    elif event_type == "research.idle":
        detail = f"Research idle ({reason})" if reason else "Research idle"
    elif event_type == "research.blocked":
        detail = f"Research blocked ({reason})" if reason else "Research blocked"
    elif event_type == "research.retry.scheduled":
        next_retry = _first_present(payload, "next_retry_at")
        detail = "Research retry scheduled"
        if next_retry:
            detail += f" for {next_retry}"
    elif event_type == "research.lock.acquired":
        detail = "Research loop lock acquired"
    elif event_type == "research.lock.released":
        detail = "Research loop lock released"
    else:
        detail = event.summary or event.event_type

    return f"[{timestamp}] {detail}"


class ExpandedStreamView(Static):
    """Focusable full-body expanded log stream."""

    can_focus = True
    BINDINGS = (
        Binding("up", "scroll_up", show=False),
        Binding("down", "scroll_down", show=False),
        Binding("pageup", "page_up", show=False),
        Binding("pagedown", "page_down", show=False),
        Binding("home", "scroll_home", show=False),
        Binding("end", "scroll_end", show=False),
        Binding("l", "jump_to_live", show=False),
    )

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__("", id=id, classes="panel-card expanded-stream-view", markup=False)
        self.border_title = "Expanded Stream"
        self.styles.height = "1fr"
        self.styles.overflow_y = "auto"
        self._display_mode = DisplayMode.OPERATOR
        self._active_panel_label = "Overview"
        self._events: EventLogView | None = None
        self._follow_live = True
        self._rendered_text = ""

    def show_snapshot(
        self,
        *,
        active_panel_label: str,
        display_mode: DisplayMode,
        events: EventLogView | None,
        live: bool = True,
    ) -> None:
        previous_scroll_y = self.scroll_y
        self._active_panel_label = active_panel_label
        self._display_mode = display_mode
        self._events = events
        self._rendered_text = self.summary_text()
        self._update_border_subtitle()
        if self.is_mounted:
            self.update(self._rendered_text)
            self.call_after_refresh(self._restore_viewport, previous_scroll_y, live)

    def on_mount(self) -> None:
        self._rendered_text = self.summary_text()
        self._update_border_subtitle()
        self.update(self._rendered_text)
        self.call_after_refresh(self._restore_viewport, self.scroll_y, True)

    @property
    def follow_live(self) -> bool:
        return self._follow_live

    @property
    def at_live_tail(self) -> bool:
        return (self.max_scroll_y - self.scroll_y) <= 1

    def summary_text(self) -> str:
        if self._display_mode is DisplayMode.DEBUG:
            return self._render_debug_text()
        return self._render_operator_text()

    def action_scroll_up(self) -> None:
        self._disengage_follow_live()
        self.scroll_up(animate=False, immediate=True)
        self._update_border_subtitle()

    def action_scroll_down(self) -> None:
        self.scroll_down(animate=False, immediate=True)
        self._update_border_subtitle()

    def action_page_up(self) -> None:
        self._disengage_follow_live()
        self.scroll_page_up(animate=False)
        self._update_border_subtitle()

    def action_page_down(self) -> None:
        self.scroll_page_down(animate=False)
        self._update_border_subtitle()

    def action_scroll_home(self) -> None:
        self._disengage_follow_live()
        self.scroll_home(animate=False, immediate=True, x_axis=False, y_axis=True)
        self._update_border_subtitle()

    def action_scroll_end(self) -> None:
        self.scroll_end(animate=False, immediate=True, x_axis=False, y_axis=True)
        self._update_border_subtitle()

    def action_jump_to_live(self) -> None:
        self._follow_live = True
        self._update_border_subtitle()
        if self.is_mounted:
            self._scroll_to_live_tail()
            self.call_after_refresh(self._scroll_to_live_tail)

    def on_mouse_scroll_up(self, event: MouseScrollUp) -> None:
        self._disengage_follow_live()
        self.scroll_up(animate=False, immediate=True)
        self._update_border_subtitle()
        event.stop()

    def on_mouse_scroll_down(self, event: MouseScrollDown) -> None:
        self.scroll_down(animate=False, immediate=True)
        self._update_border_subtitle()
        event.stop()

    def _render_operator_text(self) -> str:
        events = self._events.events if self._events is not None else ()
        live_state = "LIVE TAIL" if self._follow_live else "SCROLLBACK"
        lines = [
            f"{self._display_mode.value.upper()} EXPANDED | {self._active_panel_label}",
            f"State: {live_state}",
            (
                "Pinned to the newest lines. Press Up/PageUp/Home or scroll upward to browse older output."
                if self._follow_live
                else "Browsing older lines. New events still append off-screen until you press l to jump live."
            ),
            f"Cached runtime events: {len(events)}",
            "",
            "Narrated activity feed",
        ]
        if events:
            lines.extend(render_runtime_event_operator_line(event) for event in events)
        else:
            lines.append("No runtime events cached yet.")
        lines.append("")
        lines.append("Controls")
        lines.append("- e toggles expanded mode.")
        lines.append("- Escape exits expanded mode.")
        lines.append("- Up/Down, PageUp/PageDown, Home/End scroll.")
        lines.append("- l jumps to the live tail.")
        return "\n".join(lines)

    def _render_debug_text(self) -> str:
        events = self._events.events if self._events is not None else ()
        live_state = "LIVE TAIL" if self._follow_live else "SCROLLBACK"
        lines = [
            f"{self._display_mode.value.upper()} EXPANDED | {self._active_panel_label}",
            f"State: {live_state}",
            (
                "Expanded stream shell mount is active and pinned to the newest lines."
                if self._follow_live
                else "Expanded stream shell mount is active in scrollback; incoming lines keep appending off-screen."
            ),
            "Raw structured runtime events from the current shell event stream.",
            "This debug view stays unsynthesized even if operator rendering changes later.",
            f"Cached runtime events: {len(events)}",
            "",
            "Debug event feed",
        ]
        if events:
            lines.extend(render_runtime_event_debug_line(event) for event in events)
        else:
            lines.append("No runtime events cached yet.")
        lines.append("")
        lines.append("Controls")
        lines.append("- e toggles expanded mode.")
        lines.append("- Escape exits expanded mode.")
        lines.append("- Up/Down, PageUp/PageDown, Home/End scroll.")
        lines.append("- l jumps to the live tail.")
        return "\n".join(lines)

    def _disengage_follow_live(self) -> None:
        if self._follow_live:
            self._follow_live = False

    def _update_border_subtitle(self) -> None:
        state = "live" if self._follow_live else "scrollback"
        self.border_subtitle = f"{self._display_mode.value} | {self._active_panel_label} | {state}"

    def _restore_viewport(self, previous_scroll_y: float, live: bool) -> None:
        if not self.is_mounted:
            return
        if live and self._follow_live:
            self._scroll_to_live_tail()
            return
        target = min(previous_scroll_y, self.max_scroll_y)
        self.scroll_to(y=target, animate=False, immediate=True, force=True)

    def _scroll_to_live_tail(self) -> None:
        self.scroll_to(y=self.max_scroll_y, animate=False, immediate=True, force=True)


__all__ = ["ExpandedStreamView", "render_runtime_event_operator_line"]
