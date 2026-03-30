"""Focused modal workflow for controlled config edits."""

from __future__ import annotations

from dataclasses import dataclass

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static

from ..models import ConfigFieldInputKind, ConfigFieldView


@dataclass(frozen=True, slots=True)
class ConfigEditRequest:
    """Normalized config-edit payload returned to the shell."""

    key: str
    value: str


class ConfigEditModal(ModalScreen[ConfigEditRequest | None]):
    """Collect one validated config edit for a supported field."""

    BINDINGS = [
        ("escape", "cancel", "Cancel"),
        ("ctrl+enter", "submit", "Apply"),
    ]

    def __init__(self, *, field: ConfigFieldView, daemon_running: bool) -> None:
        super().__init__()
        self._field = field
        self._daemon_running = daemon_running

    def compose(self) -> ComposeResult:
        apply_mode = (
            "Daemon is running, so this edit will queue through the mailbox first."
            if self._daemon_running
            else "Daemon is stopped, so this edit writes directly and reloads immediately."
        )
        with Vertical(classes="modal-dialog modal-form"):
            yield Static(f"Edit {self._field.label}", classes="modal-title")
            yield Static(
                "\n".join(
                    (
                        f"Field: {self._field.key}",
                        f"Boundary: {self._field.boundary}",
                        f"Current value: {self._field.value}",
                        self._field.description,
                        apply_mode,
                        self._input_help(),
                    )
                ),
                classes="modal-copy",
            )
            yield Static("New Value", classes="modal-label")
            yield Input(
                value=self._field.value,
                placeholder=self._placeholder_text(),
                id="config-edit-value",
            )
            yield Static("", id="config-edit-error", classes="modal-error")
            with Horizontal(classes="modal-actions"):
                yield Button("Cancel", id="config-edit-cancel")
                yield Button("Apply Edit", id="config-edit-submit", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#config-edit-value", Input).focus()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_submit(self) -> None:
        value = self.query_one("#config-edit-value", Input).value.strip()
        normalized = self._normalized_value(value)
        if normalized is None:
            return
        self.dismiss(ConfigEditRequest(key=self._field.key, value=normalized))

    def _input_help(self) -> str:
        if self._field.input_kind is ConfigFieldInputKind.INTEGER:
            minimum = self._field.minimum if self._field.minimum is not None else 0
            return f"Enter an integer greater than or equal to {minimum}."
        if self._field.options:
            return "Allowed values: " + ", ".join(self._field.options)
        return "Enter the new validated value for this field."

    def _placeholder_text(self) -> str:
        if self._field.options:
            return ", ".join(self._field.options)
        if self._field.input_kind is ConfigFieldInputKind.INTEGER and self._field.minimum is not None:
            return f"Integer >= {self._field.minimum}"
        return self._field.value

    def _normalized_value(self, value: str) -> str | None:
        if not value:
            self._set_error("A value is required.")
            return None
        if self._field.input_kind is ConfigFieldInputKind.INTEGER:
            try:
                parsed = int(value)
            except ValueError:
                self._set_error("Enter a whole-number integer.")
                return None
            minimum = self._field.minimum if self._field.minimum is not None else 0
            if parsed < minimum:
                self._set_error(f"Value must be greater than or equal to {minimum}.")
                return None
            return str(parsed)
        if self._field.input_kind is ConfigFieldInputKind.CHOICE:
            normalized = value.strip().lower()
            options = {option.lower(): option for option in self._field.options}
            if normalized not in options:
                self._set_error("Choose one of: " + ", ".join(self._field.options))
                return None
            return options[normalized]
        return value

    @on(Button.Pressed, "#config-edit-cancel")
    def _handle_cancel_button(self, event: Button.Pressed) -> None:
        event.stop()
        self.action_cancel()

    @on(Button.Pressed, "#config-edit-submit")
    def _handle_submit_button(self, event: Button.Pressed) -> None:
        event.stop()
        self.action_submit()

    @on(Input.Changed, "#config-edit-value")
    def _handle_input_changed(self, _: Input.Changed) -> None:
        self._clear_error()

    def _set_error(self, message: str) -> None:
        self.query_one("#config-edit-error", Static).update(message)

    def _clear_error(self) -> None:
        self._set_error("")


__all__ = ["ConfigEditModal", "ConfigEditRequest"]
