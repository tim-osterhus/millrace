"""Keyboard and command-discovery help for the Millrace TUI shell."""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from ..action_discovery import ShellActionSurface
from ..models import PANEL_BY_ID, PanelId


class HelpModal(ModalScreen[None]):
    """Show global and active-panel keyboard guidance."""

    BINDINGS = (
        Binding("escape", "close", "Close"),
        Binding("question_mark", "close", "Close", show=False),
    )

    def __init__(self, *, active_panel: PanelId, action_surface: ShellActionSurface) -> None:
        super().__init__()
        self._active_panel = active_panel
        self._action_surface = action_surface

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal-dialog"):
            yield Static("Keyboard Help", classes="modal-title")
            yield Static(self._body_text(), id="help-modal-body", classes="modal-copy")
            with Horizontal(classes="modal-actions"):
                yield Button("Close", id="help-close", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#help-close", Button).focus()

    def action_close(self) -> None:
        self.dismiss(None)

    @on(Button.Pressed, "#help-close")
    def _handle_close_button(self, event: Button.Pressed) -> None:
        event.stop()
        self.action_close()

    def _body_text(self) -> str:
        panel = PANEL_BY_ID[self._active_panel]
        lines = [
            f"Current panel: {panel.label}",
            "",
            "Shell modes",
            "- Operator mode keeps panel content summary-first for day-to-day control.",
            "- Debug mode keeps denser technical detail for diagnosis.",
            "",
            "Signal boundaries",
            "- Persistent panel state lives in the active panel and top status bar.",
            "- Notices are short action outcomes and failure signals.",
            "- Open debug mode or detail modals when you need deeper context.",
            "",
            "Action bar",
            *[f"- {action.help_text}" for action in self._action_surface.global_actions],
            "",
            self._action_surface.context_title.title(),
        ]
        lines.extend(f"- {action.help_text}" for action in self._action_surface.context_actions)
        lines.extend(
            [
                "",
                "Operator note",
                "- The footer and action palette are intentionally limited to the highest-value actions for the current context.",
            ]
        )
        return "\n".join(lines)


__all__ = ["HelpModal"]
