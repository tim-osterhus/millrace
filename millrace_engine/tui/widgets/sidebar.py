"""Left-hand panel navigation for the Millrace shell."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Button, Static

from ..models import (
    DisplayMode,
    LifecycleSignalView,
    LifecycleState,
    PanelDefinition,
    PanelId,
    nav_button_id,
)


class SidebarNav(Static):
    """Sidebar navigation that emits typed panel-selection messages."""

    class PanelSelected(Message):
        def __init__(self, panel_id: PanelId) -> None:
            super().__init__()
            self.panel_id = panel_id

    class ModeToggleRequested(Message):
        pass

    class ExpandedToggleRequested(Message):
        pass

    _BADGE_STATE_CLASSES = (
        "state-idle",
        "state-launching-once",
        "state-launching-daemon",
        "state-running",
        "state-paused",
        "state-stop",
        "state-failure",
    )

    _BADGE_LABEL_BY_STATE = {
        LifecycleState.IDLE: "stopped",
        LifecycleState.LAUNCHING_ONCE: "launching",
        LifecycleState.LAUNCHING_DAEMON: "launching",
        LifecycleState.DAEMON_RUNNING: "running",
        LifecycleState.PAUSED: "paused",
        LifecycleState.STOP_IN_PROGRESS: "stopping",
        LifecycleState.LIFECYCLE_FAILURE: "failure",
    }

    def __init__(
        self,
        panels: tuple[PanelDefinition, ...],
        *,
        active_panel: PanelId,
        display_mode: DisplayMode = DisplayMode.OPERATOR,
        lifecycle_signal: LifecycleSignalView | None = None,
        id: str | None = None,
    ) -> None:
        super().__init__(id=id)
        self.panels = panels
        self.active_panel = active_panel
        self.display_mode = display_mode
        self.lifecycle_signal = lifecycle_signal or LifecycleSignalView(
            state=LifecycleState.IDLE,
            label="idle",
            detail="awaiting snapshot",
        )

    def compose(self) -> ComposeResult:
        with Vertical(classes="sidebar-frame"):
            yield Static("MILLRACE", classes="sidebar-wordmark")
            yield Static("daemon", classes="sidebar-kicker")
            yield Static("", id="sidebar-daemon-badge", classes="sidebar-daemon-badge")
            yield Static("panels", classes="sidebar-title")
            with Vertical(classes="sidebar-panels"):
                for panel in self.panels:
                    yield Button(panel.label, id=nav_button_id(panel.id), classes="sidebar-button")
            yield Button("", id="sidebar-mode-toggle", classes="sidebar-mode-toggle")
            yield Button("", id="sidebar-expanded-toggle", classes="sidebar-expanded-toggle")
            yield Static("? help\nctrl+p commands", classes="sidebar-help")

    def on_mount(self) -> None:
        self.set_active_panel(self.active_panel)
        self.set_display_mode(self.display_mode)
        self.set_lifecycle_signal(self.lifecycle_signal)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "sidebar-mode-toggle":
            event.stop()
            self.post_message(self.ModeToggleRequested())
            return
        if event.button.id == "sidebar-expanded-toggle":
            event.stop()
            self.post_message(self.ExpandedToggleRequested())
            return
        if event.button.id is None or not event.button.id.startswith("nav-"):
            return
        panel_id = PanelId(event.button.id.removeprefix("nav-"))
        event.stop()
        self.post_message(self.PanelSelected(panel_id))

    def focus_active_button(self) -> None:
        self.query_one(f"#{nav_button_id(self.active_panel)}", Button).focus()

    def set_active_panel(self, panel_id: PanelId) -> None:
        self.active_panel = panel_id
        for panel in self.panels:
            button = self.query_one(f"#{nav_button_id(panel.id)}", Button)
            if panel.id == panel_id:
                button.add_class("active")
            else:
                button.remove_class("active")

    def set_display_mode(self, display_mode: DisplayMode) -> None:
        self.display_mode = display_mode
        toggle = self.query_one("#sidebar-mode-toggle", Button)
        target = DisplayMode.DEBUG if display_mode is DisplayMode.OPERATOR else DisplayMode.OPERATOR
        toggle.label = f"{display_mode.value} -> {target.value}"

    def set_expanded_mode(self, expanded: bool) -> None:
        toggle = self.query_one("#sidebar-expanded-toggle", Button)
        toggle.label = "exit expanded" if expanded else "enter expanded"

    def set_lifecycle_signal(self, lifecycle_signal: LifecycleSignalView) -> None:
        self.lifecycle_signal = lifecycle_signal
        badge = self.query_one("#sidebar-daemon-badge", Static)
        state_label = self._BADGE_LABEL_BY_STATE[lifecycle_signal.state]
        badge.update(f"● {state_label.upper()}\n{self._clip_text(lifecycle_signal.detail, limit=28)}")
        for badge_class in self._BADGE_STATE_CLASSES:
            badge.remove_class(badge_class)
        state_class = {
            LifecycleState.IDLE: "state-idle",
            LifecycleState.LAUNCHING_ONCE: "state-launching-once",
            LifecycleState.LAUNCHING_DAEMON: "state-launching-daemon",
            LifecycleState.DAEMON_RUNNING: "state-running",
            LifecycleState.PAUSED: "state-paused",
            LifecycleState.STOP_IN_PROGRESS: "state-stop",
            LifecycleState.LIFECYCLE_FAILURE: "state-failure",
        }[lifecycle_signal.state]
        badge.add_class(state_class)

    @staticmethod
    def _clip_text(value: str, *, limit: int) -> str:
        normalized = " ".join(value.split())
        if len(normalized) <= limit:
            return normalized
        if limit <= 3:
            return normalized[:limit]
        return f"{normalized[: limit - 3]}..."
