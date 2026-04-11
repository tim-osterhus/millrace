"""Readable action footer for the shell."""

from __future__ import annotations

from rich.text import Text
from textual.widgets import Static

from ..action_discovery import ActionHint, ShellActionSurface


class ActionFooter(Static):
    """Render a two-line human-readable action surface."""

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__("", id=id)
        self._surface: ShellActionSurface | None = None

    def show_surface(self, surface: ShellActionSurface) -> None:
        self._surface = surface
        self.update(self._render_surface(surface))

    def _render_surface(self, surface: ShellActionSurface) -> Text:
        text = Text()
        text.append("Global", style="bold")
        self._append_actions(text, surface.global_actions)
        text.append("\n")
        text.append(surface.context_title.title(), style="bold")
        self._append_actions(text, surface.context_actions)
        return text

    @staticmethod
    def _append_actions(text: Text, actions: tuple[ActionHint, ...]) -> None:
        for action in actions:
            text.append("  ")
            text.append(f"[{action.key}]", style="bold #ef4444")
            text.append(f" {action.label}")


__all__ = ["ActionFooter"]
