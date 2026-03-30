"""Placeholder panels for the initial shell layout."""

from __future__ import annotations

from textual.widgets import Static

from ..models import PanelDefinition


class PlaceholderPanel(Static):
    """Focusable placeholder content until runtime-backed panels land."""

    can_focus = True

    def __init__(self, panel: PanelDefinition, *, id: str) -> None:
        super().__init__(self._panel_text(panel), id=id, classes="panel-card")
        self.panel = panel
        self.border_title = panel.label

    @staticmethod
    def _panel_text(panel: PanelDefinition) -> str:
        return (
            f"{panel.summary}\n\n"
            f"{panel.placeholder_body}\n\n"
            "This screen is intentionally static in Run 01 so later runs can add gateway-backed state safely."
        )

