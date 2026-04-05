"""Shared lifecycle seams for focused TUI modal screens."""

from __future__ import annotations

from typing import Generic, TypeVar

from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Static


ResultT = TypeVar("ResultT")


class ManagedModalScreen(ModalScreen[ResultT], Generic[ResultT]):
    """Lightweight base for modal focus, cancel, and error-surface behavior."""

    BINDINGS = (
        ("escape", "cancel", "Cancel"),
    )

    cancel_result: ResultT
    initial_focus_selector: str | None = None
    error_selector: str | None = None

    def on_mount(self) -> None:
        self._focus_initial()

    def action_cancel(self) -> None:
        self.dismiss(self.cancel_result)

    def _focus_initial(self) -> None:
        if self.initial_focus_selector is None:
            return
        self.query_one(self.initial_focus_selector, Widget).focus()

    def _set_error(self, message: str) -> None:
        if self.error_selector is None:
            return
        self.query_one(self.error_selector, Static).update(message)

    def _clear_error(self) -> None:
        self._set_error("")


__all__ = ["ManagedModalScreen"]
