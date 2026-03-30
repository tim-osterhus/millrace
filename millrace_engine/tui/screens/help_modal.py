"""Keyboard and command-discovery help for the Millrace TUI shell."""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from ..models import PANEL_BY_ID, PanelId

_GLOBAL_LINES = (
    "1-7 open the main panels.",
    "s focuses the sidebar and c focuses the active panel.",
    "e toggles expanded stream mode and Escape exits it.",
    "t opens Add Task and i opens Add Idea.",
    "Ctrl+P opens the command palette for panel, lifecycle, config, and publish actions.",
    "? opens or closes this help.",
)

_PANEL_LINES: dict[PanelId, tuple[str, ...]] = {
    PanelId.QUEUE: (
        "Up/Down/Home/End move through the visible backlog.",
        "Enter starts or applies a queue reorder draft for the selected task.",
        "r starts a reorder draft, [ and ] move the selected task, and Escape cancels the draft.",
        "o opens run detail for the current active run context.",
    ),
    PanelId.RUNS: (
        "Up/Down/Home/End move through recent runs.",
        "Enter opens detail for the selected run.",
    ),
    PanelId.LOGS: (
        "Up/Down/Home/End move through the filtered event list.",
        "Enter opens run detail when the selected event includes a run id.",
        "f toggles follow and freeze.",
        "Ctrl+Left/Right changes the source filter and Ctrl+Up/Down changes the event-type filter.",
    ),
    PanelId.CONFIG: (
        "Up/Down/Home/End move through editable config fields.",
        "e or Enter opens the guided edit modal for the selected field.",
        "r reloads config through the supported control path.",
    ),
    PanelId.PUBLISH: (
        "r refreshes publish preflight.",
        "g syncs the staging repo selection.",
        "n starts the local-only commit flow and p starts the higher-friction commit-and-push flow.",
    ),
}


class HelpModal(ModalScreen[None]):
    """Show global and active-panel keyboard guidance."""

    BINDINGS = (
        Binding("escape", "close", "Close"),
        Binding("question_mark", "close", "Close", show=False),
    )

    def __init__(self, *, active_panel: PanelId) -> None:
        super().__init__()
        self._active_panel = active_panel

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
            "Global shortcuts",
            *[f"- {line}" for line in _GLOBAL_LINES],
            "",
            f"{panel.label} shortcuts",
        ]
        panel_lines = _PANEL_LINES.get(self._active_panel)
        if panel_lines is None:
            lines.append("- This panel does not add extra keyboard controls.")
        else:
            lines.extend(f"- {line}" for line in panel_lines)
        lines.extend(
            [
                "",
                "Operator note",
                "- The command palette stays available even when a panel has no local shortcuts.",
            ]
        )
        return "\n".join(lines)


__all__ = ["HelpModal"]
