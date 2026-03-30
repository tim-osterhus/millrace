"""Focused modal workflow for queuing one idea source file."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static


@dataclass(frozen=True, slots=True)
class AddIdeaRequest:
    """Normalized add-idea payload returned to the shell."""

    source_path: Path


class AddIdeaModal(ModalScreen[AddIdeaRequest | None]):
    """Collect and validate one source file path before queueing an idea."""

    BINDINGS = [
        ("escape", "cancel", "Cancel"),
        ("ctrl+enter", "submit", "Submit"),
    ]

    def __init__(self, *, workspace_path: Path) -> None:
        super().__init__()
        self.workspace_path = workspace_path

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal-dialog modal-form"):
            yield Static("Add Idea", classes="modal-title")
            yield Static(
                (
                    "Queue one existing source file for research-side intake. "
                    "Relative paths resolve from the active workspace."
                ),
                classes="modal-copy",
            )
            yield Static("Idea Source File", classes="modal-label")
            yield Input(
                placeholder="notes/idea.md or /absolute/path/to/idea.md",
                id="add-idea-path",
            )
            yield Static(f"Workspace root: {self.workspace_path}", classes="modal-help")
            yield Static("", id="add-idea-error", classes="modal-error")
            with Horizontal(classes="modal-actions"):
                yield Button("Cancel", id="add-idea-cancel")
                yield Button("Queue Idea", id="add-idea-submit", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#add-idea-path", Input).focus()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_submit(self) -> None:
        raw_value = self.query_one("#add-idea-path", Input).value.strip()
        if not raw_value:
            self._set_error("Idea source path is required.")
            self.query_one("#add-idea-path", Input).focus()
            return

        source_path = Path(raw_value).expanduser()
        if not source_path.is_absolute():
            source_path = self.workspace_path / source_path
        source_path = source_path.resolve()

        if not source_path.exists():
            self._set_error(f"Idea source file does not exist: {source_path}")
            self.query_one("#add-idea-path", Input).focus()
            return
        if not source_path.is_file():
            self._set_error(f"Idea source path is not a file: {source_path}")
            self.query_one("#add-idea-path", Input).focus()
            return

        self.dismiss(AddIdeaRequest(source_path=source_path))

    @on(Button.Pressed, "#add-idea-cancel")
    def _handle_cancel_button(self, event: Button.Pressed) -> None:
        event.stop()
        self.action_cancel()

    @on(Button.Pressed, "#add-idea-submit")
    def _handle_submit_button(self, event: Button.Pressed) -> None:
        event.stop()
        self.action_submit()

    @on(Input.Changed, "#add-idea-path")
    def _handle_input_changed(self, _: Input.Changed) -> None:
        self._clear_error()

    def _set_error(self, message: str) -> None:
        self.query_one("#add-idea-error", Static).update(message)

    def _clear_error(self) -> None:
        self._set_error("")


__all__ = ["AddIdeaModal", "AddIdeaRequest"]
