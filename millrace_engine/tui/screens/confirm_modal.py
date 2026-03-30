"""Reusable confirmation modal for deliberate operator mutations."""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static


class ConfirmModal(ModalScreen[bool]):
    """Ask the operator to explicitly confirm one high-friction action."""

    BINDINGS = [
        ("escape", "cancel", "Cancel"),
    ]

    def __init__(
        self,
        *,
        title: str,
        body_lines: tuple[str, ...],
        confirm_label: str = "Confirm",
        cancel_label: str = "Cancel",
    ) -> None:
        super().__init__()
        self._title = title
        self._body_lines = body_lines
        self._confirm_label = confirm_label
        self._cancel_label = cancel_label

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal-dialog"):
            yield Static(self._title, classes="modal-title")
            yield Static("\n".join(self._body_lines), classes="modal-copy")
            with Horizontal(classes="modal-actions"):
                yield Button(self._cancel_label, id="confirm-cancel")
                yield Button(self._confirm_label, id="confirm-submit", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#confirm-cancel", Button).focus()

    def action_cancel(self) -> None:
        self.dismiss(False)

    def action_confirm(self) -> None:
        self.dismiss(True)

    @on(Button.Pressed, "#confirm-cancel")
    def _handle_cancel_button(self, event: Button.Pressed) -> None:
        event.stop()
        self.action_cancel()

    @on(Button.Pressed, "#confirm-submit")
    def _handle_submit_button(self, event: Button.Pressed) -> None:
        event.stop()
        self.action_confirm()


__all__ = ["ConfirmModal"]
