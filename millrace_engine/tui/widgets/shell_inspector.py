"""Persistent shell inspector for panel context and selection detail."""

from __future__ import annotations

from dataclasses import dataclass

from textual.widgets import Static


@dataclass(frozen=True, slots=True)
class ShellInspectorView:
    """Rendered shell-owned inspector state for the active panel."""

    panel_label: str
    title: str
    headline: str
    detail_lines: tuple[str, ...]
    action_lines: tuple[str, ...] = ()


def render_shell_inspector(view: ShellInspectorView) -> str:
    lines = [
        f"PANEL   {view.panel_label}",
        f"FOCUS   {view.title}",
        f"STATE   {view.headline}",
    ]
    if view.detail_lines:
        lines.append("")
        lines.append("DETAIL")
        lines.extend(f"- {line}" for line in view.detail_lines)
    if view.action_lines:
        lines.append("")
        lines.append("NEXT")
        lines.extend(f"- {line}" for line in view.action_lines)
    return "\n".join(lines)


class ShellInspector(Static):
    """Read-only inspector that stays mounted across panel changes."""

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__("", id=id, classes="shell-inspector", markup=False)
        self.border_title = "Inspector"

    def show_view(self, view: ShellInspectorView) -> None:
        self.border_subtitle = view.panel_label
        self.update(render_shell_inspector(view))


__all__ = ["ShellInspector", "ShellInspectorView", "render_shell_inspector"]
