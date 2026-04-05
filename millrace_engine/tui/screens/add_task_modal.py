"""Focused modal workflow for adding one execution task."""

from __future__ import annotations

from dataclasses import dataclass

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Input, Static, TextArea

from .modal_support import ManagedModalScreen


@dataclass(frozen=True, slots=True)
class AddTaskRequest:
    """Normalized add-task payload returned to the shell."""

    title: str
    spec_id: str | None = None
    body: str | None = None


class AddTaskModal(ManagedModalScreen[AddTaskRequest | None]):
    """Collect one operator-authored task card before gateway execution."""

    BINDINGS = [
        *ManagedModalScreen.BINDINGS,
        ("ctrl+enter", "submit", "Submit"),
    ]
    cancel_result = None
    initial_focus_selector = "#add-task-title"
    error_selector = "#add-task-error"

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal-dialog modal-form"):
            yield Static("Add Task", classes="modal-title")
            yield Static(
                "Create one execution backlog task with an operator-reviewed title and optional context fields.",
                classes="modal-copy",
            )
            yield Static("Task Title", classes="modal-label")
            yield Input(
                placeholder="Ship the next operator-visible milestone",
                id="add-task-title",
            )
            yield Static("Spec ID (optional)", classes="modal-label")
            yield Input(
                placeholder="SPEC-123",
                id="add-task-spec-id",
            )
            yield Static("Task Body (optional)", classes="modal-label")
            yield TextArea(
                "",
                id="add-task-body",
                classes="modal-textarea",
                placeholder="Add supporting markdown notes or acceptance details.",
                show_line_numbers=False,
            )
            yield Static("", id="add-task-error", classes="modal-error")
            with Horizontal(classes="modal-actions"):
                yield Button("Cancel", id="add-task-cancel")
                yield Button("Add Task", id="add-task-submit", variant="primary")

    def action_submit(self) -> None:
        title = self.query_one("#add-task-title", Input).value.strip()
        if not title:
            self._set_error("Task title is required.")
            self._focus_initial()
            return

        spec_id = self.query_one("#add-task-spec-id", Input).value.strip() or None
        body = self.query_one("#add-task-body", TextArea).text.strip() or None
        self.dismiss(AddTaskRequest(title=title, spec_id=spec_id, body=body))

    @on(Button.Pressed, "#add-task-cancel")
    def _handle_cancel_button(self, event: Button.Pressed) -> None:
        event.stop()
        self.action_cancel()

    @on(Button.Pressed, "#add-task-submit")
    def _handle_submit_button(self, event: Button.Pressed) -> None:
        event.stop()
        self.action_submit()

    @on(Input.Changed, "#add-task-title")
    @on(Input.Changed, "#add-task-spec-id")
    def _handle_input_changed(self, _: Input.Changed) -> None:
        self._clear_error()

    @on(TextArea.Changed, "#add-task-body")
    def _handle_body_changed(self, _: TextArea.Changed) -> None:
        self._clear_error()

__all__ = ["AddTaskModal", "AddTaskRequest"]
