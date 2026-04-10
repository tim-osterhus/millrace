"""Global key bindings for the Millrace TUI."""

from __future__ import annotations

from textual.binding import Binding

from .models import PANELS

NAVIGATION_GROUP = Binding.Group("Panels", compact=True)
FOCUS_GROUP = Binding.Group("Focus", compact=True)


APP_BINDINGS = tuple(
    [
        *[
            Binding(
                key=str(index),
                action=f"open_panel('{panel.id.value}')",
                description=panel.label,
                key_display=str(index),
                group=NAVIGATION_GROUP,
            )
            for index, panel in enumerate(PANELS, start=1)
        ],
        Binding("s", "focus_sidebar", "Sidebar", group=FOCUS_GROUP),
        Binding("c", "focus_content", "Workspace", group=FOCUS_GROUP),
        Binding("tab", "focus_next", "Next Region", key_display="Tab", group=FOCUS_GROUP),
        Binding("shift+tab", "focus_previous", "Prev Region", key_display="Shift+Tab", group=FOCUS_GROUP),
    ]
)
